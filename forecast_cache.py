# -*- coding: utf-8 -*-
"""
forecast_cache.py - מטמון תחזיות בדיסק.

מסמן ריצה לפי hash של (model, series_values, horizon, context, events_subset).
אם hash זהה לאחת שכבר רצה, מחזיר תוצאה שמורה בלי לאמן מחדש.

משמעותי בעיקר ל-Prophet ו-XGBoost שלוקחים שניות-עשרות שניות לאמן;
ARIMA מהיר ולא נכלל אם רוצים (אבל אין נזק לכלול אותו).

מבנה הקבצים:
    {cache_dir}/
        a1b2c3d4...e7.pkl    ← pickle של ה-DataFrame המוחזר
        a1b2c3d4...e7.json   ← מטא-דאטה (model, n_obs, ts) ל-debug
"""
from __future__ import annotations
import hashlib
import json
import os
import pickle
import time
from pathlib import Path
import pandas as pd

from logger import logger

CACHE_DIR = Path(os.environ.get(
    'FORECAST_CACHE_DIR',
    str(Path(__file__).parent / 'forecast_models_cache')
))
CACHE_TTL_SECONDS = int(os.environ.get('FORECAST_CACHE_TTL', 7 * 24 * 3600))  # שבוע


def _ensure_dir():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _key(model: str, series: pd.Series, horizon: int,
         events_df: pd.DataFrame, context: dict) -> str:
    """hash דטרמיניסטי של הקלטים.

    כולל גם MODEL_VERSION כך ששינוי לוגיקה בקוד המודל יבטל cache אוטומטית
    (אחרת ה-app היה מחזיר תוצאות-pickle מ-לוגיקה ישנה אחרי עדכון).
    """
    from forecast_engine import MODEL_VERSION

    h = hashlib.sha256()
    h.update(model.encode())
    h.update(MODEL_VERSION.encode())
    h.update(str(horizon).encode())

    # series - index (ym) + values בעיגול ל-2 ספרות
    for ym, val in zip(series.index, series.values):
        h.update(f"{ym}={float(val):.2f};".encode())

    # context - מסודר לפי key
    h.update(json.dumps(context, sort_keys=True, ensure_ascii=False,
                        default=str).encode())

    # events - רק חודשים שעשויים להשפיע (היסטוריה + horizon חודשים קדימה)
    if events_df is not None and not events_df.empty:
        evt = events_df.copy()
        if 'year_month' in evt.columns:
            evt = evt.sort_values('year_month')
            for _, row in evt.iterrows():
                h.update(f"|{row.to_dict()}".encode())

    return h.hexdigest()[:24]


def get(model: str, series: pd.Series, horizon: int,
        events_df: pd.DataFrame, context: dict) -> pd.DataFrame | None:
    _ensure_dir()
    key = _key(model, series, horizon, events_df, context)
    pkl = CACHE_DIR / f"{key}.pkl"
    if not pkl.exists():
        return None
    age = time.time() - pkl.stat().st_mtime
    if age > CACHE_TTL_SECONDS:
        logger.debug("forecast_cache: %s expired (%ds old)", key, age)
        return None
    try:
        with pkl.open('rb') as f:
            df = pickle.load(f)
        logger.info("forecast_cache HIT: model=%s key=%s", model, key)
        return df
    except Exception as e:
        logger.warning("forecast_cache: failed to load %s: %s", pkl, e)
        return None


def put(model: str, series: pd.Series, horizon: int,
        events_df: pd.DataFrame, context: dict,
        df: pd.DataFrame):
    _ensure_dir()
    key = _key(model, series, horizon, events_df, context)
    pkl = CACHE_DIR / f"{key}.pkl"
    meta = CACHE_DIR / f"{key}.json"
    try:
        with pkl.open('wb') as f:
            pickle.dump(df, f)
        meta.write_text(json.dumps({
            'model':   model,
            'horizon': horizon,
            'n_obs':   len(series),
            'context': context,
            'ts':      time.time(),
        }, ensure_ascii=False, default=str), encoding='utf-8')
        logger.info("forecast_cache STORE: model=%s key=%s", model, key)
    except Exception as e:
        logger.warning("forecast_cache: failed to write %s: %s", pkl, e)


def clear_all():
    """מנקה את כל המטמון (לדיבוג / אחרי שינוי גרסת מודל)."""
    if not CACHE_DIR.exists():
        return 0
    n = 0
    for f in CACHE_DIR.iterdir():
        try:
            f.unlink()
            n += 1
        except Exception:
            pass
    logger.info("forecast_cache: cleared %d files", n)
    return n


# ────────────────────────────────────────────────
#  Wrapped model functions (drop-in replacements)
# ────────────────────────────────────────────────
def cached_arima(series, horizon, events_df, context):
    df = get('arima', series, horizon, events_df, context)
    if df is not None:
        return df
    from forecast_engine import forecast_arima
    df = forecast_arima(series, horizon, events_df, context)
    put('arima', series, horizon, events_df, context, df)
    return df


def cached_prophet(series, horizon, events_df, context):
    df = get('prophet', series, horizon, events_df, context)
    if df is not None:
        return df
    from forecast_engine import forecast_prophet
    df = forecast_prophet(series, horizon, events_df, context)
    put('prophet', series, horizon, events_df, context, df)
    return df


def cached_xgboost(series, horizon, events_df, context):
    df = get('xgboost', series, horizon, events_df, context)
    if df is not None:
        return df
    from forecast_engine import forecast_xgboost
    df = forecast_xgboost(series, horizon, events_df, context)
    put('xgboost', series, horizon, events_df, context, df)
    return df
