# -*- coding: utf-8 -*-
"""
flight_schedule_scraper.py — מושך תוכניות-טיסה עתידיות מ-IAA flight-board.

הזרימה:
1. GET את דף ה-board כדי לקבל ufprt token + cookies.
2. לכל חודש בטווח הרצוי, POST search עם FromDate/ToDate/AirlineCompany.
3. סופר טיסות, מאגד לפי (year_month, airline), מכניס ל-flight_schedule
   עם UPSERT (last_scraped מתעדכן).

ה-endpoint לא מוגן ב-reCaptcha בפועל (השדה ריק) ולא בקצב. עם זאת,
ה-scraper מטיל sleep קצר בין בקשות לנימוס.

נקרא מ-nightly_sync כשלב.
"""
from __future__ import annotations
import logging
import re
import time
from collections import Counter, defaultdict
from datetime import date
from dateutil.relativedelta import relativedelta

import requests
from psycopg2.extras import execute_values

from db_config import get_conn
from logger import logger


BOARD_URL  = "https://www.iaa.gov.il/airports/ben-gurion/flight-board/"
SEARCH_URL = "https://www.iaa.gov.il/umbraco/surface/FlightBoardSurface/Search"

# ה-search מקבל IATA code ריק = כל החברות. נשתמש בזה לאחת-בקשה-לחודש
# במקום בקשה-לחברה. סופרים תוצאות מקובצים גם לפי airline.
USER_AGENT      = 'Mozilla/5.0 (memsi-flight-scraper)'
REQUEST_TIMEOUT = 30
SLEEP_BETWEEN   = 1.0   # שניות בין בקשות, נימוס


def _build_session() -> tuple[requests.Session, str, list[tuple[str, str]]]:
    """GET דף ה-board. מחזיר (session, ufprt, insured_airlines).

    insured_airlines: רק החברות שאנחנו מבטחים — שאר הטיסות לא מייצרות
    לקוחות אצלנו. הרשימה מ-insured_airlines.INSURED_AIRLINES.
    ה-API מחזיר 500 אם AirlineCompany ריק, אז עוברים airline-by-airline."""
    from insured_airlines import INSURED_AIRLINES

    session = requests.Session()
    session.headers.update({
        'User-Agent': USER_AGENT,
        'Accept-Language': 'he-IL,he;q=0.9,en;q=0.7',
    })
    r = session.get(BOARD_URL, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()

    tokens = re.findall(
        r"name='ufprt'\s+type='hidden'\s+value='([^']+)'", r.text
    )
    if not tokens:
        raise RuntimeError("ufprt token not found in IAA board HTML")

    airlines = list(INSURED_AIRLINES.items())
    return session, tokens[-1], airlines


def _fetch_month_airline(session: requests.Session, ufprt: str,
                          year: int, month: int, airline_code: str) -> list[dict]:
    """POST search לחודש שלם של חברה ספציפית. ה-API מחזיר 500 אם
    AirlineCompany ריק, אז חייבים לעבור airline אחר airline."""
    last_day = (date(year, month, 1) + relativedelta(months=1, days=-1)).day
    form = {
        'g-recaptcha-response': '',
        'FlightType':       'Incoming',
        'AirportId':        'LLBG',
        'UICulture':        'he-IL',
        'City':             '',
        'Country':          '',
        'AirlineCompany':   airline_code,
        'FromDate':         f'1/{month}/{year}',
        'ToDate':           f'{last_day}/{month}/{year}',
        'ufprt':            ufprt,
    }
    r = session.post(SEARCH_URL, data=form, timeout=REQUEST_TIMEOUT)
    if r.status_code in (400, 500):
        # 500 = חברה ללא טיסות בחודש. 400 = validation (date past).
        # שני המקרים: נחשיב כ-0 ולא נכשיל את הסקרייפ.
        return []
    r.raise_for_status()
    data = r.json()
    return data.get('Flights', [])


# מיפוי שם-חברה ל-IATA code. ל-IATA יש קוד 2-3 תווים שאנחנו רוצים
# (קומפקטי). השם המלא מ-API גנרי-מדי וקשה לטיפול. הקובץ הזה לא ממצה,
# מוסיף לפי הצורך.
_AIRLINE_NAME_TO_IATA = {
    'EL AL ISRAEL AIRLINES':            'LY',
    'ARKIA ISRAELI AIRLINES':           'IZ',
    'ISRAIR':                            '6H',
    'AEGEAN AIRLINES':                   'A3',
    'AIR FRANCE':                        'AF',
    'LUFTHANSA':                         'LH',
    'BRITISH AIRWAYS':                   'BA',
    'KLM':                               'KL',
    'SWISS':                             'LX',
    'AUSTRIAN AIRLINES':                 'OS',
    'TURKISH AIRLINES':                  'TK',
    'RYANAIR':                           'FR',
    'WIZZ AIR':                          'W6',
    'EASYJET':                           'U2',
    'TUS AIRWAYS':                       'U8',
    'BLUE BIRD AIRWAYS':                 'BZ',
    'AZERBAIJAN AIRLINES':               'J2',
    'DELTA AIR LINES':                   'DL',
    'AMERICAN AIRLINES':                 'AA',
    'UNITED AIRLINES':                   'UA',
    'AIR EUROPA':                        'UX',
    'AIR CANADA':                        'AC',
    'AEROFLOT':                          'SU',
    'CATHAY PACIFIC':                    'CX',
    'EMIRATES':                          'EK',
    'ETIHAD AIRWAYS':                    'EY',
    'ROYAL JORDANIAN':                   'RJ',
    'TAP PORTUGAL':                      'TP',
    'VIRGIN ATLANTIC':                   'VS',
    'BRUSSELS AIRLINES':                 'SN',
    'IBERIA':                            'IB',
    'SUN D OR':                          '7L',
    'AIR INDIA':                         'AI',
    'GEORGIAN AIRWAYS':                  'A9',
    'WIZZ AIR MALTA':                    'W4',
    'BLUE PANORAMA':                     'BV',
    'ITA AIRWAYS':                       'AZ',
    'AIRBALTIC':                         'BT',
    'PEGASUS':                           'PC',
}


def _to_iata(airline_name: str) -> str:
    """ממיר שם-חברה ל-IATA code אם ידוע, אחרת מחזיר את שם החברה
    (לאחר נורמליזציה)."""
    key = airline_name.strip().upper()
    return _AIRLINE_NAME_TO_IATA.get(key, key[:20])  # מגביל אורך


def scrape_months(months_ahead: int = 12, lg: logging.Logger | None = None) -> dict:
    """מחזיר dict עם summary. ה-records כבר נכתבו ל-DB.

    סקרייפ ב-loop כפול: airline × month. עם 60-80 חברות × 12 חודשים = ~800
    בקשות + 1 sec sleep ≈ 13-14 דקות. נימוסי, וזה ירוץ פעם ביום ב-nightly."""
    lg = lg or logger
    session, ufprt, airlines = _build_session()
    lg.info("flight scraper: session ready, ufprt=%s..., %d airlines",
            ufprt[:20], len(airlines))

    # ה-API מחזיר 400 על תאריכי-עבר ולעיתים גם על חודש-נוכחי כשרובו כבר עבר.
    # מתחילים מהחודש-הבא (FromDate = 1 לחודש N+1).
    today = date.today()
    months: list[tuple[int, int, str]] = []
    cur = (today.replace(day=1) + relativedelta(months=1))
    for _ in range(months_ahead):
        months.append((cur.year, cur.month, cur.strftime('%Y-%m')))
        cur = cur + relativedelta(months=1)

    counts: dict[tuple[str, str], int] = defaultdict(int)
    total_counts: dict[str, int] = defaultdict(int)
    failures: list[str] = []

    total_requests = len(months) * len(airlines)
    done = 0

    for year, month, ym in months:
        for airline_code, _airline_name in airlines:
            done += 1
            try:
                flights = _fetch_month_airline(session, ufprt, year, month, airline_code)
                n = len(flights)
                if n > 0:
                    counts[(ym, airline_code)] = n
                    total_counts[ym] += n
            except Exception as e:
                failures.append(f"{ym}/{airline_code}: {type(e).__name__}: {e}")
                lg.warning("flight scraper failed for %s/%s: %s",
                           ym, airline_code, e)
            time.sleep(SLEEP_BETWEEN)
        lg.info("flight scraper: %s → %d total flights (%d/%d requests done)",
                ym, total_counts[ym], done, total_requests)

    rows = []
    for (ym, code), n in counts.items():
        rows.append((ym, code, n))
    for ym, n in total_counts.items():
        rows.append((ym, 'TOTAL', n))

    if rows:
        with get_conn() as conn:
            with conn.cursor() as cur:
                execute_values(cur, """
                    INSERT INTO flight_schedule (year_month, airline_code, planned_flights)
                    VALUES %s
                    ON CONFLICT (year_month, airline_code) DO UPDATE SET
                        planned_flights = EXCLUDED.planned_flights,
                        last_scraped    = NOW()
                """, rows)

    return {
        'months_scraped':  len(months),
        'airlines':        len(airlines),
        'records':         len(rows),
        'total_flights':   sum(total_counts.values()),
        'failures':        len(failures),
    }


def sync_flight_schedule(lg: logging.Logger | None = None) -> dict:
    """Entry point ל-nightly_sync — חתימה תואמת ל-sync_priority_rolling/sync_partbal."""
    return scrape_months(months_ahead=12, lg=lg or logger)


if __name__ == '__main__':
    import json
    result = scrape_months(months_ahead=12)
    print(json.dumps(result, ensure_ascii=False, indent=2))
