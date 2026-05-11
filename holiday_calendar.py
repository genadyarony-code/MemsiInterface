# -*- coding: utf-8 -*-
"""
holiday_calendar.py — חישוב חגים יהודיים דינמית עם pyluach.

עד גרסה הקודמת, החגים היו hardcoded ב-forecast_events.csv עד 2026.
ב-2027 המערכת הייתה ממשיכה לעבוד בלי warning, אבל החודשים עם חגים לא
היו מסומנים, מה שמשמעו ש-Prophet/XGBoost לא ידעו על תקופות-שיא של
חופשת חגים.

הפתרון: get_jewish_holiday_months(year_from, year_to) מחזירה set של
year_month strings ('2026-04') שיש בהם חג גדול לפחות. המשתמש יכול לטעון
את ה-set הזה, להמיר ל-events_df, ולמזג עם forecast_events.csv (שמכיל
overrides ידניים לאירועים לא-יהודיים כמו מלחמה).

ה-API שמור פשוט: רק החודשים. אם יידרש per-day encoding בעתיד, נרחיב.
"""
from __future__ import annotations
from datetime import date
from pyluach import dates


# חגים מרכזיים: (חודש-עברי, יום-תחילה, אורך-בימים). הימים נמשכים מ-eve-1 ועד
# סוף החג כדי שגם ערב-חג ייספר.
# חודש-עברי לפי pyluach: 1=Nisan, 2=Iyar, 3=Sivan, 4=Tammuz, 5=Av, 6=Elul,
# 7=Tishrei, 8=Cheshvan, 9=Kislev, 10=Tevet, 11=Shevat, 12=Adar (13=Adar II).
_MAJOR_HOLIDAYS = [
    # (שם, חודש, יום, אורך_בימים)
    ('Pesach',        1,  15, 8),   # 15-22 Nisan
    ('Shavuot',       3,   6, 2),   # 6-7 Sivan
    ('Rosh Hashana',  7,   1, 2),   # 1-2 Tishrei
    ('Yom Kippur',    7,  10, 1),   # 10 Tishrei
    ('Sukkot',        7,  15, 8),   # 15-22 Tishrei (כולל שמיני עצרת)
    ('Hanukkah',      9,  25, 8),   # 25 Kislev - 2/3 Tevet
    ('Purim',        12,  14, 1),   # 14 Adar (בשנה מעוברת — 14 Adar II)
]


def get_jewish_holiday_months(year_from: int, year_to: int) -> set[str]:
    """מחזיר set של year_month strings (YYYY-MM) שיש בהם חג גדול לפחות חלקית.

    year_from/year_to הם שנים גרגוריאניות. הפונקציה מכסה את כל החגים שנופלים
    בטווח. אם חג חוצה חודשים, שני החודשים מסומנים.
    """
    months: set[str] = set()
    # שנים עבריות שיכולות לחפוף ל-Gregorian הזה. שנה עברית מתחילה
    # ב-Tishrei בסתיו הגרגוריאני; ספציפית, HY = GY+3760 בערך.
    hy_start = year_from + 3760 - 1
    hy_end   = year_to + 3761
    for hy in range(hy_start, hy_end + 1):
        for _name, hm, hd, length in _MAJOR_HOLIDAYS:
            try:
                # שנה מעוברת: Purim בפועל בחודש Adar II (13)
                if hm == 12 and dates.HebrewDate(hy, 13, 1).month == 13:
                    use_hm = 13
                else:
                    use_hm = hm
                start_h = dates.HebrewDate(hy, use_hm, hd)
            except (ValueError, KeyError):
                # אם החודש לא קיים בשנה הזאת (אדר ב' רק במעוברת), דלג.
                continue
            for offset in range(length):
                d = start_h + offset
                g = d.to_pydate()
                if year_from <= g.year <= year_to:
                    months.add(f"{g.year}-{g.month:02d}")
    return months


def is_jewish_holiday_month(year_month: str) -> bool:
    """בודק האם year_month ('YYYY-MM') הוא חודש עם חג גדול לפחות חלקית."""
    y = int(year_month[:4])
    return year_month in get_jewish_holiday_months(y, y)
