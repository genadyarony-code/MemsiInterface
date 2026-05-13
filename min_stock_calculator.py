# -*- coding: utf-8 -*-
"""
min_stock_calculator.py — מלאי מינימום מומלץ פר (סניף × קטגוריה).

הגדרת הקטגוריה: identify_luggage על תיאור-המוצר (מותג × חומר × גודל).

סניפים זכאים:
    ≥3 תנועות-לקוח-מבוטח ב-12 חודשים אחרונים, או
    ≥1 בחודש האחרון.
    (לקוח-מבוטח = כל custname שמופיע ב-customers table.)

קצב יומי (rate):
    rate_1m  = יציאות (SH+IN) ל-30 ימים אחרונים / 30
    rate_3m  = יציאות ל-90 ימים אחרונים / 90
    rate_12m = יציאות ל-365 ימים אחרונים / 365
    rate_used = max(rate_1m, rate_3m)  ← מבטיח שלא נחסר ב-spike-נוכחי

מלאי מינימום:
    min_stock = max(1, ceil(rate_used × lead_time_days × 1.5))
    1.5 = safety factor. 1 = ברירת-מחדל מינימום.
"""
from __future__ import annotations
import math
import warnings
from datetime import date, timedelta
from collections import defaultdict

import pandas as pd

from db_config import get_conn
from domain_repository import identify_luggage

# psycopg2 conn ב-pandas read_sql_query מצביע אזהרה לא רלוונטית — מסתירים.
warnings.filterwarnings('ignore', message='pandas only supports SQLAlchemy')


# ============================================================
#  סדר היררכי להצגת הטבלה: גודל → מותג → חומר
# ============================================================
_SIZE_ORDER = ['טרולי', 'עליה', 'קטנה', 'בינונית', 'גדולה', 'ענקית']
_BRAND_ORDER = ['קלאסית', 'מותג על', 'מותג']
_MATERIAL_ORDER = ['רכה', 'קשיחה', 'בד']


def _category_sort_key(cat: str) -> tuple[int, int, int, str]:
    """Sort key: (size_rank, brand_rank, material_rank, raw_string)."""
    if not cat:
        return (99, 99, 99, '')

    size_rank = 99
    for i, s in enumerate(_SIZE_ORDER):
        if cat.startswith(s + ' '):
            size_rank = i
            break

    brand_rank = 99
    for i, b in enumerate(_BRAND_ORDER):
        if (' ' + b + ' ') in (' ' + cat + ' '):
            brand_rank = i
            break

    material_rank = 99
    for i, m in enumerate(_MATERIAL_ORDER):
        if cat.endswith(' ' + m) or cat == m:
            material_rank = i
            break

    return (size_rank, brand_rank, material_rank, cat)


# ============================================================
#  Eligible branches
# ============================================================
def _eligible_warehouses() -> list[str]:
    """מחזיר רשימת מחסנים זכאים: יש להם תנועה-של-לקוח-מבוטח כפי שמוגדר.

    תנועה-של-לקוח-מבוטח = שורת logfile עם custname בטבלת customers,
    שיש לה JOIN ל-logfile_full עם warhsname (סניף-מקור) ו-towarhsname=NULL
    (כלומר יציאה ללקוח, לא העברה-בין-מחסנים).
    """
    today = date.today()
    one_month_ago = (today - timedelta(days=30)).isoformat()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                WITH insured_docs AS (
                    SELECT DISTINCT lf.logdocno, lf.partname
                    FROM logfile lf
                    INNER JOIN customers c ON lf.custname = c.code AND c.is_active
                ),
                exits AS (
                    SELECT ff.warhsname AS branch, ff.curdate::date AS d
                    FROM logfile_full ff
                    INNER JOIN insured_docs id
                            ON ff.logdocno = id.logdocno AND ff.partname = id.partname
                    WHERE ff.warhsname IS NOT NULL
                      AND ff.towarhsname IS NULL
                      AND ff.curdate >= (CURRENT_DATE - INTERVAL '365 days')
                )
                SELECT branch,
                       SUM(CASE WHEN d >= %s THEN 1 ELSE 0 END) AS m1,
                       COUNT(*) AS y1
                FROM exits
                GROUP BY branch
                HAVING COUNT(*) >= 3 OR SUM(CASE WHEN d >= %s THEN 1 ELSE 0 END) >= 1
                ORDER BY branch
            """, (one_month_ago, one_month_ago))
            rows = cur.fetchall()
    return [r[0] for r in rows]


# ============================================================
#  Per-(branch, category, day) exits
# ============================================================
def _consumption_dataframe() -> pd.DataFrame:
    """מחזיר DataFrame של יציאות פר (warhsname, partname, curdate, qty).

    כולל logfile_full כדי לתפוס גם יציאות-מסטים (שיתפרקו לקטגוריות-רכיב
    דרך BOM). מסונן ל-365 ימים אחרונים.
    """
    today = date.today()
    cutoff = (today - timedelta(days=365)).isoformat()

    with get_conn() as conn:
        df = pd.read_sql_query(
            """
            SELECT warhsname AS branch,
                   partname  AS sku,
                   curdate::date AS day,
                   tquant   AS qty,
                   logdocno
            FROM logfile_full
            WHERE curdate >= %s
              AND warhsname IS NOT NULL
              AND towarhsname IS NULL          -- יציאה (לא העברה-בין-מחסנים)
              AND logdocno NOT LIKE 'IC%%'    -- לא ספירת-מלאי
            """,
            conn, params=(cutoff,)
        )

    # IK = "החזרה" (קונה מחזיר). qty<0 = יחידה חזרה למלאי, qty>0 = יצירת
    # תיעוד אחר. אנחנו מתעניינים ביציאות-נטו: לכן IK עם qty<0 (היפוך)
    # מצמצמת את הצריכה. נשמור הכל ונחבר כ-net consumption.
    if df.empty:
        return df

    # SH / IN = יציאה (qty>0 בדרך-כלל)
    # IK עם qty<0 = החזרה — מפחית את הצריכה
    # net_qty: positive consumption
    df['is_ik'] = df['logdocno'].str.startswith('IK', na=False)
    # net_qty = qty רגיל, אבל ל-IK הופכים את הסימן (qty=-1 ב-IK = החזרה = -1 צריכה)
    df['net_qty'] = df['qty']
    return df[['branch', 'sku', 'day', 'net_qty']]


# ============================================================
#  SKU → description → category mapping
# ============================================================
def _sku_to_category() -> dict[str, str]:
    """sku → identify_luggage(תיאור). מק"טים ללא תיאור/ללא זיהוי מסוננים."""
    with get_conn() as conn:
        df = pd.read_sql_query(
            """
            SELECT DISTINCT ON (partname) partname AS sku, topartdes AS desc
            FROM logfile
            WHERE topartdes IS NOT NULL
            ORDER BY partname, curdate DESC
            """,
            conn
        )
    out: dict[str, str] = {}
    for _, row in df.iterrows():
        cat = identify_luggage(row['desc'])
        if cat:
            out[row['sku']] = cat
    return out


# ============================================================
#  Kit → child mapping
# ============================================================
def _kit_bom_map() -> dict[str, list[str]]:
    """parent_sku → [child_sku, ...] (לסטים)."""
    out: dict[str, list[str]] = defaultdict(list)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT parent_sku, child_sku FROM kit_bom")
            for parent, child in cur.fetchall():
                out[parent].append(child)
    return dict(out)


# ============================================================
#  Current inventory
# ============================================================
def _current_inventory() -> dict[tuple[str, str], float]:
    """(branch, sku) → quantity."""
    with get_conn() as conn:
        df = pd.read_sql_query(
            "SELECT warehouse_code AS branch, sku, quantity FROM local_inventory",
            conn
        )
    return {(r['branch'], r['sku']): float(r['quantity']) for _, r in df.iterrows()}


# ============================================================
#  Main
# ============================================================
def compute_min_stock(lead_time_days: int = 7,
                     safety_factor: float = 1.5) -> pd.DataFrame:
    """מחשב טבלת המלצת מלאי-מינימום.

    החזרה: DataFrame עם עמודות:
        branch, category, rate_1m, rate_3m, rate_12m, rate_used,
        current_stock, recommended_min, gap
    """
    eligible = _eligible_warehouses()
    if not eligible:
        return pd.DataFrame()

    sku_to_cat = _sku_to_category()
    bom = _kit_bom_map()
    inv = _current_inventory()
    cons = _consumption_dataframe()
    if cons.empty:
        return pd.DataFrame()

    cons = cons[cons['branch'].isin(eligible)].copy()
    if cons.empty:
        return pd.DataFrame()

    # פירוק סטים: שורת-יציאה של parent_sku מתפרקת לקטגוריות של רכיביה.
    def _to_categories(sku: str) -> list[str]:
        cats = []
        if sku in bom:
            for child in bom[sku]:
                c = sku_to_cat.get(child)
                if c:
                    cats.append(c)
        else:
            c = sku_to_cat.get(sku)
            if c:
                cats.append(c)
        return cats

    # בנייה: לכל שורה ב-cons, מצמדים רשימת-קטגוריות. שורה אחת יכולה להיות
    # 1 קטגוריה (רכיב) או 3 (סט: L+M+S).
    rows = []
    for _, r in cons.iterrows():
        cats = _to_categories(r['sku'])
        for c in cats:
            rows.append((r['branch'], c, r['day'], float(r['net_qty'])))
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=['branch', 'category', 'day', 'qty'])

    # חישוב rates פר חלון.
    today = date.today()
    cut_1m  = today - timedelta(days=30)
    cut_3m  = today - timedelta(days=90)
    cut_12m = today - timedelta(days=365)

    def _sum_after(group_df, cutoff):
        return float(group_df.loc[group_df['day'] >= cutoff, 'qty'].sum())

    # מבנה לעזרה: (branch, category) → current_stock
    cat_inv: dict[tuple[str, str], float] = defaultdict(float)
    for (branch, sku), q in inv.items():
        cat = sku_to_cat.get(sku)
        if cat:
            cat_inv[(branch, cat)] += q

    agg = []
    for (branch, category), g in df.groupby(['branch', 'category']):
        s1  = _sum_after(g, cut_1m)
        s3  = _sum_after(g, cut_3m)
        s12 = _sum_after(g, cut_12m)
        rate_1m  = max(s1, 0) / 30.0
        rate_3m  = max(s3, 0) / 90.0
        rate_12m = max(s12, 0) / 365.0
        rate_used = max(rate_1m, rate_3m)
        recommended_min = max(1, math.ceil(rate_used * lead_time_days * safety_factor))

        current = cat_inv.get((branch, category), 0.0)

        agg.append({
            'branch': branch,
            'category': category,
            'rate_1m': round(rate_1m, 3),
            'rate_3m': round(rate_3m, 3),
            'rate_12m': round(rate_12m, 3),
            'rate_used': round(rate_used, 3),
            'current_stock': round(current, 1),
            'recommended_min': recommended_min,
            'gap': round(current - recommended_min, 1),
        })

    out = pd.DataFrame(agg)
    out['_cat_key'] = out['category'].map(_category_sort_key)
    out = out.sort_values(['branch', '_cat_key'], ascending=[True, True])
    out = out.drop(columns=['_cat_key']).reset_index(drop=True)
    return out


if __name__ == '__main__':
    df = compute_min_stock(lead_time_days=7)
    print(df.head(30).to_string())
    print(f'\nTotal rows: {len(df)}')
