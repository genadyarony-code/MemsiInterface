-- 004_forecast_runs_legacy.sql
-- מסמן את כל ה-forecast_runs שנעשו לפני תיקון הבאג ב-forecast_engine._extend_events
-- (Sprint A1, v0.1.0).
--
-- רקע: בגרסאות קודמות, _extend_events דרס אירועים היסטוריים שכבר הופיעו ב-events_df
-- בערכים-default מתוך ה-context שהמשתמש בחר. תוצאה: backtest עבור כל run שחלון
-- ה-test שלו נפל על חודשים עם אירועים ידועים (מלחמה, חג, וכו') חישב metrics
-- לא-אמינים. ה-runs האלה משאירים שמורים, אבל מסומנים כ-legacy כדי שיהיה ברור
-- שאין להסתמך עליהם להחלטות-רכש.

ALTER TABLE forecast_runs
    ADD COLUMN IF NOT EXISTS is_legacy BOOLEAN NOT NULL DEFAULT FALSE;

UPDATE forecast_runs SET is_legacy = TRUE WHERE is_legacy = FALSE;
-- לאחר ריצת המיגרציה הזאת, ריצות עתידיות נכתבות עם is_legacy=FALSE כברירת-מחדל.

CREATE INDEX IF NOT EXISTS idx_forecast_runs_is_legacy
    ON forecast_runs (is_legacy);
