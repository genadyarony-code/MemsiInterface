# -*- coding: utf-8 -*-
"""
domain_repository.py - שכבת data access לטבלאות הdomain שנוצרו ב-001+002.

מחליף את הקריאות הישירות ל-dict-ים ב-pricing_data.py / branch_names.py /
warehouse_config.py / product_identification.py. הקבצים הישנים הפכו ל-shims
שעוטפים את הפונקציות כאן.

הרעיון:
- read APIs מחזירים dict-ים שמתאימים בדיוק ל-shape הישן (תאימות לאחור).
- כל read עובר דרך cache בזיכרון; cache מתנקה ב-invalidate() לאחר write.
- writes עוברים INSERT ... ON CONFLICT DO UPDATE עם רשימה ל-domain_audit_log.
"""
from __future__ import annotations
import os
import threading
import time
import json
from typing import Any
import psycopg2.extras

from db_config import get_conn
from logger import logger


# ────────────────────────────────────────────────
#  In-memory cache עם TTL ו-invalidation גלובלי
# ────────────────────────────────────────────────
# TTL בשניות. ברירת מחדל: 5 דקות. ניתן לשנות דרך משתנה סביבה ל-debug
# (למשל 0 כדי לבטל cache, או 3600 ל-deployments חד-משתמשיים).
_CACHE_TTL_SECONDS = float(os.environ.get('DOMAIN_CACHE_TTL_SECONDS', 300))

_cache_lock = threading.RLock()  # reentrant - _load_X may itself call _cached
_cache: dict[str, tuple[float, Any]] = {}  # key → (loaded_at, value)


def _cached(key: str, loader):
    """Cache בזיכרון עם TTL. אם הערך טרי, מוחזר מיד; אחרת נטען מחדש.

    הסיבה ל-TTL ולא 'לעולם עד invalidate' היא תמיכה ב-1-2 משתמשים מקבילים:
    בלי TTL, עדכון של User A לא יתפשט ל-User B עד שהוא יסגור וייפתח את ה-app.
    עם TTL של 5 דקות, התפשטות אוטומטית בתוך זמן סביר.
    """
    now = time.monotonic()
    entry = _cache.get(key)
    if entry is not None and (now - entry[0]) < _CACHE_TTL_SECONDS:
        return entry[1]
    with _cache_lock:
        entry = _cache.get(key)
        if entry is not None and (now - entry[0]) < _CACHE_TTL_SECONDS:
            return entry[1]
        value = loader()
        _cache[key] = (time.monotonic(), value)
        return value


def invalidate(*keys: str):
    """מנקה מפתחות ספציפיים, או הכול אם לא ניתן ארגומנט."""
    with _cache_lock:
        if not keys:
            _cache.clear()
            return
        for k in keys:
            _cache.pop(k, None)


# ────────────────────────────────────────────────
#  Audit
# ────────────────────────────────────────────────
def _audit(cur, table_name: str, op: str, key: dict,
           old: dict | None, new: dict | None, user: str | None):
    cur.execute("""
        INSERT INTO domain_audit_log
            (table_name, operation, key_json, old_values, new_values, changed_by)
        VALUES (%s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s)
    """, (
        table_name, op,
        json.dumps(key, ensure_ascii=False),
        json.dumps(old, ensure_ascii=False) if old else None,
        json.dumps(new, ensure_ascii=False) if new else None,
        user,
    ))


# ────────────────────────────────────────────────
#  Customers + pricing tiers
# ────────────────────────────────────────────────
def get_customer_pricing_tier(customer_code: str) -> str | None:
    """מחזיר את ה-tier (ELAL / DELTA / ...) של לקוח, או None."""
    mapping = _cached('customer_tier_map', _load_customer_tier_map)
    return mapping.get(str(customer_code))


def _load_customer_tier_map() -> dict[str, str]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT code, pricing_tier FROM customers WHERE is_active = TRUE"
        )
        return {code: tier for code, tier in cur.fetchall()}


def list_customers() -> list[dict]:
    """מחזיר את כל הלקוחות עם ה-tier."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT code, pricing_tier, name, is_active, notes,
                       updated_by, updated_at
                FROM customers ORDER BY code
            """)
            return [dict(r) for r in cur.fetchall()]


def list_pricing_tiers() -> list[str]:
    return _cached('pricing_tiers', _load_pricing_tiers)


def _load_pricing_tiers() -> list[str]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT code FROM pricing_tiers WHERE is_active = TRUE ORDER BY code"
        )
        return [row[0] for row in cur.fetchall()]


# ────────────────────────────────────────────────
#  Customer pricing - repair + replacement
# ────────────────────────────────────────────────
def get_repair_price(customer_code: str, part_sku: str) -> float | None:
    tier = get_customer_pricing_tier(customer_code)
    if tier is None:
        return None
    table = _cached('repair_prices', _load_repair_prices)
    return table.get(tier, {}).get(str(part_sku))


def get_replacement_price(customer_code: str, luggage_type: str) -> float | None:
    tier = get_customer_pricing_tier(customer_code)
    if tier is None:
        return None
    table = _cached('replacement_prices', _load_replacement_prices)
    return table.get(tier, {}).get(str(luggage_type))


def is_repair_item(part_sku: str) -> bool:
    """בודק אם מק"ט מופיע באחד מ-tier-ים של תיקון."""
    table = _cached('repair_prices', _load_repair_prices)
    sku = str(part_sku)
    return any(sku in tier_prices for tier_prices in table.values())


def _load_repair_prices() -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT pricing_tier, part_sku, price FROM customer_repair_prices"
        )
        for tier, sku, price in cur.fetchall():
            out.setdefault(tier, {})[sku] = float(price)
    return out


def _load_replacement_prices() -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT pricing_tier, luggage_type, price FROM customer_replacement_prices"
        )
        for tier, ltype, price in cur.fetchall():
            out.setdefault(tier, {})[ltype] = float(price)
    return out


def upsert_customer_repair_price(tier: str, part_sku: str, price: float,
                                 user: str | None = None):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT price FROM customer_repair_prices
             WHERE pricing_tier = %s AND part_sku = %s
        """, (tier, part_sku))
        old = cur.fetchone()
        old_val = {'price': float(old[0])} if old else None

        cur.execute("""
            INSERT INTO customer_repair_prices
                (pricing_tier, part_sku, price, updated_by, updated_at)
            VALUES (%s, %s, %s, %s, NOW())
            ON CONFLICT (pricing_tier, part_sku) DO UPDATE
              SET price = EXCLUDED.price,
                  updated_by = EXCLUDED.updated_by,
                  updated_at = NOW()
        """, (tier, part_sku, price, user))

        _audit(cur, 'customer_repair_prices',
               'UPDATE' if old else 'INSERT',
               {'pricing_tier': tier, 'part_sku': part_sku},
               old_val, {'price': float(price)}, user)
        conn.commit()
    invalidate('repair_prices', 'repair_skus')
    logger.info("upsert repair_price: tier=%s sku=%s price=%s by=%s",
                tier, part_sku, price, user)


def upsert_customer_replacement_price(tier: str, luggage_type: str,
                                      price: float, user: str | None = None):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT price FROM customer_replacement_prices
             WHERE pricing_tier = %s AND luggage_type = %s
        """, (tier, luggage_type))
        old = cur.fetchone()
        old_val = {'price': float(old[0])} if old else None

        cur.execute("""
            INSERT INTO customer_replacement_prices
                (pricing_tier, luggage_type, price, updated_by, updated_at)
            VALUES (%s, %s, %s, %s, NOW())
            ON CONFLICT (pricing_tier, luggage_type) DO UPDATE
              SET price = EXCLUDED.price,
                  updated_by = EXCLUDED.updated_by,
                  updated_at = NOW()
        """, (tier, luggage_type, price, user))

        _audit(cur, 'customer_replacement_prices',
               'UPDATE' if old else 'INSERT',
               {'pricing_tier': tier, 'luggage_type': luggage_type},
               old_val, {'price': float(price)}, user)
        conn.commit()
    invalidate('replacement_prices')
    logger.info("upsert replacement_price: tier=%s type=%s price=%s by=%s",
                tier, luggage_type, price, user)


# ────────────────────────────────────────────────
#  Supplier pricing
# ────────────────────────────────────────────────
def get_supplier_repair_price(part_sku: str) -> float | None:
    table = _cached('supplier_repair', _load_supplier_repair)
    return table.get(str(part_sku))


def get_supplier_replacement_price(luggage_type: str) -> float | None:
    table = _cached('supplier_replacement', _load_supplier_replacement)
    return table.get(str(luggage_type))


def get_supplier_payment(part_sku: str | None, luggage_type: str | None,
                         quantity: int) -> float | None:
    """תאימות לאחור: pricing_data.get_supplier_payment."""
    if part_sku and is_repair_item(part_sku):
        price = get_supplier_repair_price(part_sku)
        return price * quantity if price is not None else None
    if luggage_type:
        price = get_supplier_replacement_price(luggage_type)
        return price * quantity if price is not None else None
    return None


def _load_supplier_repair() -> dict[str, float]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT part_sku, price FROM supplier_repair_prices")
        return {sku: float(p) for sku, p in cur.fetchall()}


def _load_supplier_replacement() -> dict[str, float]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT luggage_type, price FROM supplier_replacement_prices")
        return {lt: float(p) for lt, p in cur.fetchall()}


def upsert_supplier_repair_price(part_sku: str, price: float,
                                 user: str | None = None):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT price FROM supplier_repair_prices WHERE part_sku = %s",
                    (part_sku,))
        old = cur.fetchone()
        old_val = {'price': float(old[0])} if old else None
        cur.execute("""
            INSERT INTO supplier_repair_prices (part_sku, price, updated_by, updated_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (part_sku) DO UPDATE
              SET price = EXCLUDED.price,
                  updated_by = EXCLUDED.updated_by,
                  updated_at = NOW()
        """, (part_sku, price, user))
        _audit(cur, 'supplier_repair_prices',
               'UPDATE' if old else 'INSERT',
               {'part_sku': part_sku}, old_val, {'price': float(price)}, user)
        conn.commit()
    invalidate('supplier_repair', 'repair_skus')


def upsert_supplier_replacement_price(luggage_type: str, price: float,
                                      user: str | None = None):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT price FROM supplier_replacement_prices WHERE luggage_type = %s",
            (luggage_type,))
        old = cur.fetchone()
        old_val = {'price': float(old[0])} if old else None
        cur.execute("""
            INSERT INTO supplier_replacement_prices
                (luggage_type, price, updated_by, updated_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (luggage_type) DO UPDATE
              SET price = EXCLUDED.price,
                  updated_by = EXCLUDED.updated_by,
                  updated_at = NOW()
        """, (luggage_type, price, user))
        _audit(cur, 'supplier_replacement_prices',
               'UPDATE' if old else 'INSERT',
               {'luggage_type': luggage_type}, old_val,
               {'price': float(price)}, user)
        conn.commit()
    invalidate('supplier_replacement')


# ────────────────────────────────────────────────
#  Branches
# ────────────────────────────────────────────────
def get_branch_name(code: str) -> str:
    """מחזיר שם תצוגה לקוד סניף, או את הקוד עצמו."""
    table = _cached('branches', _load_branches)
    return table.get(str(code).strip(), str(code))


def get_display_label(code: str) -> str:
    """'שם (קוד)' אם יש שם, אחרת רק הקוד."""
    table = _cached('branches', _load_branches)
    name = table.get(str(code).strip())
    if name:
        return f"{name} ({code})"
    return str(code)


def list_branches() -> dict[str, str]:
    return dict(_cached('branches', _load_branches))


def _load_branches() -> dict[str, str]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT code, name FROM branches WHERE is_active = TRUE ORDER BY code"
        )
        return {code: name for code, name in cur.fetchall()}


# ────────────────────────────────────────────────
#  Warehouses
# ────────────────────────────────────────────────
def list_warehouses(active_only: bool = True) -> dict[int, str]:
    key = 'warehouses_active' if active_only else 'warehouses_all'
    return dict(_cached(key, lambda: _load_warehouses(active_only)))


def _load_warehouses(active_only: bool) -> dict[int, str]:
    sql = "SELECT code, name FROM warehouses"
    if active_only:
        sql += " WHERE is_active = TRUE"
    sql += " ORDER BY code"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql)
        return {int(code): name for code, name in cur.fetchall()}


# ────────────────────────────────────────────────
#  Luggage identification
# ────────────────────────────────────────────────
def identify_luggage(description: str) -> str | None:
    """מחזיר קטגוריה לתיאור מוצר.
    משתמש ב-regex substring matching (כמו המימוש המקורי) - תיאור הקלט
    יכול להיות חלק מהתיאור הרשום, וגם לכלול וריאציות רווחים."""
    if not description:
        return None
    patterns = _cached('luggage_patterns', _load_luggage_patterns)
    clean = ' '.join(str(description).split())
    for pat, cat in patterns:
        if pat.search(clean):
            return cat
    return None


def _load_luggage_patterns():
    import re
    by_cat = _cached('luggage_by_category', _load_luggage_by_category)
    pairs = []
    for cat, descs in by_cat.items():
        for desc in descs:
            normalized = ' '.join(desc.split())
            pairs.append((re.compile(re.escape(normalized), re.IGNORECASE), cat))
    # סדר לפי אורך יורד כדי שדפוסים ארוכים וספציפיים ינצחו קצרים
    pairs.sort(key=lambda t: -len(t[0].pattern))
    return pairs


def list_luggage_categories() -> list[str]:
    """כל הקטגוריות הייחודיות."""
    return _cached('luggage_categories', _load_luggage_categories)


def list_luggage_descriptions(category: str) -> list[str]:
    """כל התיאורים בקטגוריה מסוימת."""
    by_cat = _cached('luggage_by_category', _load_luggage_by_category)
    return list(by_cat.get(category, []))


def _load_luggage_id() -> dict[str, str]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT description, category FROM luggage_identification")
        return {desc: cat for desc, cat in cur.fetchall()}


def _load_luggage_categories() -> list[str]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT category FROM luggage_identification ORDER BY category"
        )
        return [row[0] for row in cur.fetchall()]


def _load_luggage_by_category() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT category, description
              FROM luggage_identification
             ORDER BY category, description
        """)
        for cat, desc in cur.fetchall():
            out.setdefault(cat, []).append(desc)
    return out


def list_repair_part_skus() -> list[str]:
    """כל ה-SKU-ים שיש להם מחיר תיקון (לפחות אצל לקוח אחד)."""
    return _cached('repair_skus', _load_repair_skus)


def _load_repair_skus() -> list[str]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT part_sku FROM customer_repair_prices
            UNION
            SELECT DISTINCT part_sku FROM supplier_repair_prices
            ORDER BY 1
        """)
        return [r[0] for r in cur.fetchall()]


# ────────────────────────────────────────────────
#  Audit log reader
# ────────────────────────────────────────────────
def get_recent_audit(limit: int = 50, table_name: str | None = None) -> list[dict]:
    """מחזיר רשומות אודיט אחרונות, החדשות קודם."""
    sql = """
        SELECT id, table_name, operation, key_json, old_values, new_values,
               changed_by, changed_at
        FROM domain_audit_log
    """
    params: list = []
    if table_name:
        sql += " WHERE table_name = %s"
        params.append(table_name)
    sql += " ORDER BY changed_at DESC LIMIT %s"
    params.append(limit)
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]


# ────────────────────────────────────────────────
#  Current user (for audit attribution)
# ────────────────────────────────────────────────
def get_current_user() -> str:
    """מחזיר את ה-Windows username של המשתמש הנוכחי, ל-audit attribution."""
    import os
    return os.environ.get('USERNAME') or os.environ.get('USER') or 'unknown'


def add_luggage_identification(description: str, category: str,
                               user: str | None = None):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT category FROM luggage_identification WHERE description = %s",
            (description,))
        old = cur.fetchone()
        old_val = {'category': old[0]} if old else None
        cur.execute("""
            INSERT INTO luggage_identification
                (description, category, updated_by, updated_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (description) DO UPDATE
              SET category = EXCLUDED.category,
                  updated_by = EXCLUDED.updated_by,
                  updated_at = NOW()
        """, (description, category, user))
        _audit(cur, 'luggage_identification',
               'UPDATE' if old else 'INSERT',
               {'description': description}, old_val,
               {'category': category}, user)
        conn.commit()
    invalidate('luggage_id', 'luggage_categories',
               'luggage_by_category', 'luggage_patterns')
    logger.info("add luggage_id: '%s' as %s by=%s", description, category, user)
