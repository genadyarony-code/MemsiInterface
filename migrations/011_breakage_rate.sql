-- 011_breakage_rate.sql
-- Sprint C2.8: pre-calibrated rates למודל הסיבתי.
--
-- rate = repairs / n_core_branches / arriving_passengers × 100,000
-- כלומר: כמה תיקונים-לסניף-לכל-100K-נחיתות.
--
-- הערכים נמדדו על 9 סניפי-הליבה (2024-2025), MAPE ~15% על backtest.

CREATE TABLE IF NOT EXISTS breakage_rate (
    regime           TEXT PRIMARY KEY,         -- 'HIGH' / 'MEDIUM' / 'LOW'
    rate             NUMERIC(8,2) NOT NULL,    -- repairs per branch per 100K arrivals
    std_dev          NUMERIC(8,2),             -- ל-confidence intervals
    description      TEXT,
    sample_months    INTEGER,                  -- כמה חודשים-היסטוריים נכנסו לחישוב
    last_calibrated  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO breakage_rate (regime, rate, std_dev, description, sample_months) VALUES
    ('HIGH',   22.65, 3.27, 'Backlog burst / war-normalized (2024-Q1..Q3)', 8),
    ('MEDIUM', 11.87, 1.15, 'Ceasefire stabilization / transition (Q1-2025, 2026)', 3),
    ('LOW',     7.79, 1.40, 'Post-trauma new normal (Q3-2025)', 5)
ON CONFLICT (regime) DO UPDATE SET
    rate            = EXCLUDED.rate,
    std_dev         = EXCLUDED.std_dev,
    description     = EXCLUDED.description,
    sample_months   = EXCLUDED.sample_months,
    last_calibrated = NOW();
