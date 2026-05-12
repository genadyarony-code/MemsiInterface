-- 010_flight_schedule.sql
-- Sprint C2.6: תוכניות-טיסה עתידיות מ-IAA flight-board.
-- בנפרד מ-flight_traffic (שמכיל היסטוריה מ-monthly PDFs).
--
-- flight_traffic = עבר (counts מ-PDFs)
-- flight_schedule = עתיד (planned flights מ-board)
--
-- הסקרייפר מעדכן יומית כחלק מ-nightly_sync. last_scraped מציין מתי
-- הנתון נדגם אחרון. אם user רוצה לדעת אם הנתון טרי — בודק את העמודה הזו.

CREATE TABLE IF NOT EXISTS flight_schedule (
    year_month       TEXT NOT NULL,             -- '2026-06'
    airline_code     TEXT NOT NULL,             -- 'LY' / 'A3' / 'TOTAL' (אגרגט-של-כולן)
    planned_flights  INTEGER NOT NULL,          -- מספר נחיתות מתוכננות בחודש
    last_scraped     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (year_month, airline_code)
);

CREATE INDEX IF NOT EXISTS idx_flight_schedule_ym
    ON flight_schedule (year_month);
CREATE INDEX IF NOT EXISTS idx_flight_schedule_scraped
    ON flight_schedule (last_scraped DESC);
