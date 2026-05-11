# -*- coding: utf-8 -*-
"""
forecast_evaluation.py
- backtest: train/test split על סדרה היסטורית, מחשב MAE/RMSE/MAPE לכל מודל.
- save_run: שומר ריצת תחזית מלאה ב-forecast_runs / _predictions / _metrics.
- get_run_history: רשימת ריצות אחרונות עבור UI.

עובד עם forecast_engine הקיים - אין שינוי באלגוריתמים, רק evaluation סביבם.
"""
from __future__ import annotations
import json
from typing import Callable
import numpy as np
import pandas as pd
from psycopg2.extras import execute_values

from db_config import get_conn
from logger import logger
from forecast_engine import forecast_arima, forecast_prophet, forecast_xgboost


_MODEL_FNS: dict[str, Callable] = {
    'arima':   forecast_arima,
    'prophet': forecast_prophet,
    'xgboost': forecast_xgboost,
}


# ────────────────────────────────────────────────
#  Metrics
# ────────────────────────────────────────────────
def _mae(actual: np.ndarray, predicted: np.ndarray) -> float:
    return float(np.mean(np.abs(actual - predicted)))


def _rmse(actual: np.ndarray, predicted: np.ndarray) -> float:
    return float(np.sqrt(np.mean((actual - predicted) ** 2)))


def _mape(actual: np.ndarray, predicted: np.ndarray) -> float | None:
    """MAPE - מוגדר רק כשactual!=0; מחזיר None אם אין שורה תקפה."""
    mask = actual != 0
    if not mask.any():
        return None
    return float(np.mean(np.abs((actual[mask] - predicted[mask]) / actual[mask])) * 100.0)


# ────────────────────────────────────────────────
#  Backtest
# ────────────────────────────────────────────────
def backtest(series: pd.Series, events_df: pd.DataFrame, context: dict,
             test_size: int = 6) -> dict[str, dict]:
    """
    מאמן כל מודל על series[:-test_size] וחוזה את החלק האחרון.
    מחזיר dict: {model: {'mae': X, 'rmse': Y, 'mape': Z|None, 'test_n': N}}.
    אם הסדרה קצרה מדי - מחזיר dict ריק (אין מספיק נתונים).
    """
    if len(series) < test_size + 6:
        logger.info("backtest: series too short (%d < %d+6), skipping",
                    len(series), test_size)
        return {}

    train = series.iloc[:-test_size]
    actual = series.iloc[-test_size:].values.astype(float)
    metrics: dict[str, dict] = {}

    for name, fn in _MODEL_FNS.items():
        try:
            df = fn(train, test_size, events_df, context)
            pred = df['forecast'].values.astype(float)[:test_size]
            metrics[name] = {
                'test_n': int(test_size),
                'mae':    _mae(actual, pred),
                'rmse':   _rmse(actual, pred),
                'mape':   _mape(actual, pred),
            }
        except Exception as e:
            logger.exception("backtest %s failed", name)
            metrics[name] = {
                'test_n': int(test_size),
                'mae':    None, 'rmse': None, 'mape': None,
                'error':  f"{type(e).__name__}: {e}",
            }

    return metrics


# ────────────────────────────────────────────────
#  Persistence
# ────────────────────────────────────────────────
def save_run(branches: list[str], categories: list[str],
             horizon_months: int, context: dict, series_n: int,
             results: dict, metrics: dict | None = None,
             ran_by: str | None = None, notes: str | None = None) -> int:
    """
    שומר ריצת תחזית ב-forecast_runs (+ predictions + metrics).
    results: dict[model -> DataFrame(year_month, forecast, lower, upper)]
             שווה לפלט של forecast_engine.run_all_models.
    מחזיר run_id.
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO forecast_runs
                (ran_by, branches, categories, horizon_months,
                 context_json, series_n, notes)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s)
            RETURNING run_id
        """, (
            ran_by or _current_user(),
            branches, categories,
            horizon_months,
            json.dumps(context, ensure_ascii=False),
            series_n,
            notes,
        ))
        run_id = cur.fetchone()[0]

        # predictions — bulk insert עם execute_values
        pred_rows = []
        for model_name in ('arima', 'prophet', 'xgboost'):
            df = results.get(model_name)
            if df is None or df.empty:
                continue
            for _, row in df.iterrows():
                pred_rows.append((
                    run_id, model_name,
                    str(row['year_month']),
                    float(row['forecast']),
                    float(row['lower']) if pd.notna(row.get('lower')) else None,
                    float(row['upper']) if pd.notna(row.get('upper')) else None,
                ))

        # 'avg' (ממוצע 3 המודלים) לצרכי השוואה אחר כך
        models_with_data = [m for m in ('arima', 'prophet', 'xgboost')
                            if m in results and not results[m].empty]
        if len(models_with_data) >= 2:
            base = results[models_with_data[0]]
            avg_forecast = sum(results[m]['forecast'].values
                               for m in models_with_data) / len(models_with_data)
            for ym, val in zip(base['year_month'].tolist(), avg_forecast):
                pred_rows.append((run_id, 'avg', str(ym), float(val), None, None))

        if pred_rows:
            execute_values(cur, """
                INSERT INTO forecast_predictions
                    (run_id, model, year_month, forecast, lower, upper)
                VALUES %s
                ON CONFLICT (run_id, model, year_month) DO NOTHING
            """, pred_rows)

        # metrics — bulk insert
        if metrics:
            metric_rows = [
                (
                    run_id, model_name,
                    m.get('test_n'),
                    m.get('mae'),
                    m.get('rmse'),
                    m.get('mape'),
                )
                for model_name, m in metrics.items()
            ]
            if metric_rows:
                execute_values(cur, """
                    INSERT INTO forecast_metrics
                        (run_id, model, test_n, mae, rmse, mape)
                    VALUES %s
                    ON CONFLICT (run_id, model) DO NOTHING
                """, metric_rows)

        conn.commit()

    logger.info("forecast run saved: id=%d horizon=%d branches=%s",
                run_id, horizon_months, branches)
    return run_id


def get_run_history(limit: int = 30) -> pd.DataFrame:
    """מחזיר DataFrame של ריצות אחרונות (ל-UI ב-updates/forecast tab)."""
    with get_conn() as conn:
        return pd.read_sql_query("""
            SELECT
                r.run_id, r.ran_at, r.ran_by,
                array_length(r.branches, 1)   AS n_branches,
                array_length(r.categories, 1) AS n_categories,
                r.horizon_months, r.series_n,
                COALESCE(m.mae,  0) AS arima_mae,
                COALESCE(m2.mae, 0) AS prophet_mae,
                COALESCE(m3.mae, 0) AS xgboost_mae
            FROM forecast_runs r
            LEFT JOIN forecast_metrics m  ON m.run_id  = r.run_id AND m.model  = 'arima'
            LEFT JOIN forecast_metrics m2 ON m2.run_id = r.run_id AND m2.model = 'prophet'
            LEFT JOIN forecast_metrics m3 ON m3.run_id = r.run_id AND m3.model = 'xgboost'
            ORDER BY r.ran_at DESC
            LIMIT %s
        """, conn, params=(limit,))


def _current_user() -> str:
    import os
    return os.environ.get('USERNAME') or os.environ.get('USER') or 'unknown'
