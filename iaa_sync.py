# -*- coding: utf-8 -*-
"""
iaa_sync.py — מוריד דו"חות חודשיים מ-IAA ומכניס ל-flight_traffic.

הסקריפט הזה רץ פעם בחודש (כחלק מ-nightly_sync) ובודק אם יש דו"ח חודש חדש
ב-https://www.iaa.gov.il/about/aeronautical-information/annualreport/.
אם יש — מוריד את ה-PDF, מחלץ את ה-aggregate stats, ומכניס ל-DB.

הלוגיקה של החילוץ זהה לסוכן ש-extracted את ה-historical batch ב-C1.5.
"""
from __future__ import annotations
import re
import time
from pathlib import Path
from datetime import date
from dateutil.relativedelta import relativedelta

import requests
from psycopg2.extras import execute_values

from db_config import get_conn
from logger import logger


IAA_INDEX_URL = "https://www.iaa.gov.il/about/aeronautical-information/annualreport/"
PDF_CACHE_DIR = Path(__file__).parent / '.iaa_pdfs'

USER_AGENT = 'Mozilla/5.0 (memsi-nightly-sync)'
REQUEST_TIMEOUT = 30


def _which_months_to_check() -> list[str]:
    """מחזיר רשימת year_month שצריך לבדוק. הלוגיקה:
    מה החודש האחרון שיש ב-flight_traffic עם notes='ok'? נבדוק את כל החודשים
    מאז ועד החודש שלפני-נוכחי. נסה גם את החודש הקודם-לקודם — IAA יכולה לפרסם
    דו"ח מאוחר."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT MAX(year_month) FROM flight_traffic
                WHERE notes = 'ok'
            """)
            last_ok = cur.fetchone()[0]
    if last_ok:
        start = (date.fromisoformat(last_ok + '-01') + relativedelta(months=1))
    else:
        start = date(2022, 1, 1)
    end = date.today().replace(day=1) - relativedelta(months=1)
    months = []
    cur = start
    while cur <= end:
        months.append(cur.strftime('%Y-%m'))
        cur += relativedelta(months=1)
    return months


def _fetch_index() -> dict[str, str]:
    """מוריד את ה-index של IAA ומחזיר dict {'YYYY-MM': pdf_url}."""
    r = requests.get(IAA_INDEX_URL,
                     headers={'User-Agent': USER_AGENT, 'Accept-Language': 'he,en'},
                     timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    html = r.text

    # מילים בעברית לחודשים
    HEB_MONTHS = {
        'ינואר': 1, 'פברואר': 2, 'מרץ': 3, 'מארס': 3,
        'אפריל': 4, 'מאי': 5, 'יוני': 6, 'יולי': 7,
        'אוגוסט': 8, 'ספטמבר': 9, 'אוקטובר': 10,
        'נובמבר': 11, 'דצמבר': 12,
    }

    # מחפש href של PDF + טקסט סמוך עם חודש+שנה
    # IAA-pattern: <a href="/media/.../doch-XXX-MONTH-YEAR-*.pdf">MONTH</a>
    out: dict[str, str] = {}
    # מילון רחב: כל href של /media/... .pdf, ולאחר מכן ננסה לחלץ שנה+חודש
    # מהשם של הקובץ או מטקסט סביבו.
    for m in re.finditer(r'href="(/media/[^"]+\.pdf)"', html):
        href = m.group(1)
        # שנה — 4 ספרות 20XX
        year_m = re.search(r'(20\d{2})', href)
        if not year_m:
            continue
        year = int(year_m.group(1))
        # חודש: מנסה למצוא שם-חודש בעברית בתוך ה-href
        month: int | None = None
        for heb, num in HEB_MONTHS.items():
            if heb in href:
                month = num
                break
        # fallback: דפוס YYYY-MM (לא תמיד)
        if month is None:
            mm = re.search(r'20\d{2}[-_/]?(\d{1,2})\b', href)
            if mm:
                month = int(mm.group(1))
        if month and 1 <= month <= 12:
            ym = f"{year}-{month:02d}"
            full = 'https://www.iaa.gov.il' + href if href.startswith('/') else href
            # אם כבר קיים, השומר את ה-first match (סדר מהאתר)
            out.setdefault(ym, full)
    return out


def _download_pdf(url: str, local_path: Path):
    PDF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    r = requests.get(url, headers={'User-Agent': USER_AGENT},
                     timeout=REQUEST_TIMEOUT, stream=True)
    r.raise_for_status()
    tmp = local_path.with_suffix('.tmp')
    with tmp.open('wb') as f:
        for chunk in r.iter_content(chunk_size=8192):
            f.write(chunk)
    tmp.replace(local_path)


def _extract_pdf_metrics(pdf_path: Path) -> dict | None:
    """מחלץ aggregate metrics מ-PDF. אותה לוגיקה כמו extraction של C1.5.
    מחזיר None אם לא הצליח (image-only PDF, פורמט בלתי-צפוי)."""
    try:
        import pdfplumber
    except ImportError:
        logger.error("pdfplumber not installed; cannot extract PDF metrics")
        return None

    try:
        with pdfplumber.open(pdf_path) as pdf:
            text_blocks = []
            for page in pdf.pages[:8]:  # רוב הנתונים בעמ' 5
                t = page.extract_text() or ''
                text_blocks.append(t)
        full_text = '\n'.join(text_blocks)
    except Exception as e:
        logger.warning("failed to open PDF %s: %s", pdf_path.name, e)
        return None

    if len(full_text.strip()) < 100:
        logger.info("PDF %s appears image-only (text=%d chars)",
                    pdf_path.name, len(full_text.strip()))
        return None

    # מחלץ מספרים גדולים בעלי 6-7 ספרות (נוסעים) או 4-5 ספרות (טיסות)
    # סורק שורה-שורה ומחפש את הדפוס של "Ben-Gurion table" (3 שורות-טיסות
    # ואז 4 שורות-נוסעים, כל שורה עם total בקצה).
    rows = []
    for line in full_text.split('\n'):
        # נקה (cid:N) glyphs
        clean = re.sub(r'\(cid:\d+\)', ' ', line)
        nums = re.findall(r'-?\d[\d,]*', clean)
        nums = [int(n.replace(',', '')) for n in nums if n.replace(',', '').lstrip('-').isdigit()]
        if nums:
            rows.append(nums)

    # מחפש blocks של 7 שורות שמתאימים לדפוס
    # שורות 0-2: arr_flights, dep_flights, total_flights (sum check)
    # שורות 3-6: arr_pax, dep_pax, transit_pax, total_pax (sum check)
    candidates = []
    for i in range(len(rows) - 6):
        block = rows[i:i+7]
        try:
            arr_f, dep_f, tot_f = block[0][-1], block[1][-1], block[2][-1]
            arr_p, dep_p, tr_p, tot_p = block[3][-1], block[4][-1], block[5][-1], block[6][-1]
        except IndexError:
            continue
        # sanity: שורות-טיסות מסתכמות לטוטל, ושורות-נוסעים מסתכמות לטוטל
        if abs((arr_f + dep_f) - tot_f) > 2:
            continue
        if abs((arr_p + dep_p + tr_p) - tot_p) > tot_p * 0.02:
            continue
        # sanity: גדלים פלאוסיביליים
        if not (50_000 < tot_p < 5_000_000):
            continue
        if not (500 < tot_f < 50_000):
            continue
        candidates.append({
            'total_flights':       tot_f,
            'arriving_flights':    arr_f,
            'total_passengers':    tot_p,
            'arriving_passengers': arr_p,
        })

    if not candidates:
        logger.warning("no valid table found in %s", pdf_path.name)
        return None
    # המועמד הראשון הוא בדרך-כלל הנכון (עמ' 5, BG table).
    return candidates[0]


def sync_one_month(year_month: str, pdf_url: str) -> dict:
    """מוריד PDF, מחלץ, מכניס ל-DB. מחזיר dict עם status + פרטים."""
    PDF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    local = PDF_CACHE_DIR / f"iaa_{year_month}.pdf"
    if not local.exists():
        try:
            _download_pdf(pdf_url, local)
        except Exception as e:
            return {'year_month': year_month, 'status': 'download_failed', 'error': str(e)}

    metrics = _extract_pdf_metrics(local)
    if not metrics:
        # שומר שורה עם NULLs + הסבר, כך שלא ננסה שוב כל לילה
        notes = 'extraction failed (image-only or unparseable)'
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO flight_traffic
                        (year_month, source_url, notes)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (year_month) DO UPDATE SET
                        source_url = EXCLUDED.source_url,
                        notes = EXCLUDED.notes,
                        updated_at = NOW()
                """, (year_month, pdf_url, notes))
        return {'year_month': year_month, 'status': 'extraction_failed'}

    # sanity range check לפני כתיבה. ערכי-קצה אמיתיים (כמו מרץ 2026 = 203K
    # בגלל סגירת שמיים) צריכים להישמר אבל מסומנים, כדי שנדע שלא בטעות-פרסור.
    notes = 'ok'
    issues = []
    if not (300_000 < metrics['total_passengers'] < 3_000_000):
        issues.append(f"total_passengers={metrics['total_passengers']} out of range")
    if not (3_000 < metrics['total_flights'] < 25_000):
        issues.append(f"total_flights={metrics['total_flights']} out of range")
    if issues:
        notes = '; '.join(issues)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO flight_traffic
                    (year_month, total_passengers, arriving_passengers,
                     total_flights, arriving_flights, source_url, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (year_month) DO UPDATE SET
                    total_passengers    = EXCLUDED.total_passengers,
                    arriving_passengers = EXCLUDED.arriving_passengers,
                    total_flights       = EXCLUDED.total_flights,
                    arriving_flights    = EXCLUDED.arriving_flights,
                    source_url          = EXCLUDED.source_url,
                    notes               = EXCLUDED.notes,
                    updated_at          = NOW()
            """, (
                year_month,
                metrics['total_passengers'],
                metrics['arriving_passengers'],
                metrics['total_flights'],
                metrics['arriving_flights'],
                pdf_url,
                notes,
            ))
    return {
        'year_month': year_month,
        'status': 'ok' if notes == 'ok' else 'ok_with_warning',
        'metrics': metrics,
        'notes': notes,
    }


def sync_iaa_monthly() -> dict:
    """ה-entry point. מחזיר summary dict."""
    months = _which_months_to_check()
    if not months:
        return {'months_checked': 0, 'months_synced': 0}
    try:
        index = _fetch_index()
    except Exception as e:
        logger.exception("failed to fetch IAA index")
        return {'months_checked': 0, 'months_synced': 0, 'error': str(e)}

    results = []
    for ym in months:
        if ym not in index:
            logger.debug("IAA index has no PDF for %s yet", ym)
            continue
        logger.info("syncing IAA month %s from %s", ym, index[ym])
        try:
            r = sync_one_month(ym, index[ym])
            results.append(r)
        except Exception as e:
            logger.exception("sync_one_month failed for %s", ym)
            results.append({'year_month': ym, 'status': 'error', 'error': str(e)})
        time.sleep(1.0)  # נימוס מול השרת

    n_ok = sum(1 for r in results if r['status'] in ('ok', 'ok_with_warning'))
    return {
        'months_checked': len(months),
        'months_attempted': len(results),
        'months_synced': n_ok,
        'details': results,
    }


if __name__ == '__main__':
    import json
    res = sync_iaa_monthly()
    print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
