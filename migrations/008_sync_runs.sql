-- 008_sync_runs.sql
-- טבלת מעקב לריצות הסקריפט הלילי.
-- כל ריצה רושמת שורה: מתי התחילה, מתי הסתיימה, מה נמשך, מה נכשל.
-- ה-GUI קורא את השורה האחרונה ב-status-bar כדי להציג "נתונים נכון ל-X".

CREATE TABLE IF NOT EXISTS sync_runs (
    run_id          SERIAL PRIMARY KEY,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at     TIMESTAMPTZ,                       -- NULL אם עוד פעיל / קרס בלי לסיים
    status          TEXT NOT NULL DEFAULT 'running',   -- running / ok / partial / failed
    records_pulled  JSONB,                              -- {"documents": 12345, "logfile": 67890, "iaa_months": 1, ...}
    errors_count    INTEGER DEFAULT 0,
    last_error_text TEXT,
    triggered_by    TEXT,                               -- 'scheduler' / 'manual' / username
    duration_seconds INTEGER                            -- מחושב ב-finalize
);

CREATE INDEX IF NOT EXISTS idx_sync_runs_started ON sync_runs (started_at DESC);
CREATE INDEX IF NOT EXISTS idx_sync_runs_status  ON sync_runs (status);
