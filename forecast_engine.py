# -*- coding: utf-8 -*-
"""
forecast_engine.py

ממשק אחיד לכל מודלי התחזית.
כל מודל מקבל:
    series      — pd.Series עם index מסוג YYYY-MM (str) וערכים int
    horizon     — מספר חודשים לחזות קדימה
    events_df   — DataFrame של forecast_events (לצירוף פיצ'רים)
    context     — dict עם מצב נוכחי (is_war, is_military_op, ...)

ומחזיר pd.DataFrame עמודות: year_month, forecast, lower, upper
"""

import warnings
import numpy as np
import pandas as pd
from dateutil.relativedelta import relativedelta
from datetime import datetime
from logger import logger

warnings.filterwarnings('ignore')


# ────────────────────────────────────────────────
#  עזרים משותפים
# ────────────────────────────────────────────────

def _extend_events(events_df: pd.DataFrame, last_ym: str,
                   horizon: int, context: dict) -> pd.DataFrame:
    """מוסיף שורות עתידיות ל-events_df לפי context הנוכחי.

    אירועים היסטוריים שכבר מופיעים ב-events_df שומרים על ערכיהם המקוריים — שורות
    עתידיות מסונפות רק לחודשים שאינם קיימים. זה קריטי ל-backtest: כש-train_end
    נמצא בעבר, חלון ה-horizon של ה-backtest נופל על חודשים שיש להם נתוני-עבר
    ב-events_df. בלי הסינון, ה-context-default היה דורס את העובדות ההיסטוריות
    ופוגם ב-metrics.
    """
    existing_yms = (set(events_df['year_month'].astype(str))
                    if 'year_month' in events_df.columns else set())
    rows = []
    cur = datetime.strptime(last_ym + "-01", "%Y-%m-%d")
    for _ in range(horizon):
        cur = (cur + relativedelta(months=1))
        ym  = cur.strftime("%Y-%m")
        if ym in existing_yms:
            continue  # לא לדרוס נתונים היסטוריים ב-events_df
        _w = int(context.get('is_war', 0))
        _o = int(context.get('is_military_op', 0))
        _c = int(context.get('is_ceasefire', 0))
        rows.append({
            'year_month':      ym,
            'is_war':          _w,
            'is_military_op':  _o,
            'is_ceasefire':    _c,
            'jewish_holiday':  int(context.get('jewish_holiday', 0)),
            'season':          int(context.get('season', _infer_season(ym))),
            'is_summer_peak':  int(context.get('is_summer_peak',
                                               1 if cur.month in (7, 8) else 0)),
            'travel_impact':   context.get('travel_impact', 'normal'),
            'is_routine':      int(not (_w or _o or _c)),
            'is_black_friday': int(cur.month == 11),
        })
    if not rows:
        return events_df
    future = pd.DataFrame(rows)
    return pd.concat([events_df, future], ignore_index=True)


def _infer_season(ym: str) -> int:
    month = int(ym.split('-')[1])
    if month in (12, 1, 2):  return 1   # חורף
    if month in (3, 4, 5):   return 2   # אביב
    if month in (6, 7, 8):   return 3   # קיץ
    return 4                             # סתיו


def _travel_impact_num(val) -> float:
    mapping = {
        'collapse': -2.0, 'very_low': -1.5, 'low': -0.5,
        'recovering': 0.3, 'normal': 1.0, 'high': 1.5,
    }
    return mapping.get(str(val), 1.0)


def _future_months(last_ym: str, horizon: int) -> list[str]:
    cur = datetime.strptime(last_ym + "-01", "%Y-%m-%d")
    months = []
    for _ in range(horizon):
        cur = (cur + relativedelta(months=1))
        months.append(cur.strftime("%Y-%m"))
    return months


def _result_df(months: list[str], forecast: np.ndarray,
               lower: np.ndarray | None = None,
               upper: np.ndarray | None = None) -> pd.DataFrame:
    n = len(months)
    f = np.maximum(forecast[:n], 0).round().astype(int)
    l = np.maximum(lower[:n],   0).round().astype(int) if lower  is not None else f
    u = np.maximum(upper[:n],   0).round().astype(int) if upper  is not None else f
    return pd.DataFrame({'year_month': months, 'forecast': f,
                         'lower': l, 'upper': u})


# ────────────────────────────────────────────────
#  מודל 1 — ARIMA
# ────────────────────────────────────────────────
ARIMA_DESCRIPTION = (
    "ARIMA — ניתוח מגמה + עונתיות מסדרת הזמן ההיסטורית."
    " מתאים לנתונים סדירים, רגיש לשינויים פתאומיים."
)

def forecast_arima(series: pd.Series, horizon: int,
                   events_df: pd.DataFrame, context: dict) -> pd.DataFrame:
    from statsmodels.tsa.statespace.sarimax import SARIMAX

    y = series.values.astype(float)
    months = _future_months(series.index[-1], horizon)

    # SARIMA(1,1,1)(1,1,1,12) — עונתיות שנתית
    try:
        model  = SARIMAX(y, order=(1,1,1), seasonal_order=(1,1,1,12),
                         enforce_stationarity=False, enforce_invertibility=False)
        result = model.fit(disp=False)
        fc     = result.get_forecast(steps=horizon)
        pred   = fc.predicted_mean
        ci     = fc.conf_int(alpha=0.2)
        return _result_df(months, pred, ci.iloc[:,0].values, ci.iloc[:,1].values)
    except Exception as e:
        logger.exception("ARIMA failed (n=%d horizon=%d): %s, falling back to MA(6)", len(y), horizon, e)
        avg = float(np.mean(y[-6:]))
        df = _result_df(months, np.full(horizon, avg))
        df.attrs['fallback'] = f"ARIMA: {type(e).__name__}: {e}"
        return df


# ────────────────────────────────────────────────
#  מודל 2 — Prophet
# ────────────────────────────────────────────────
PROPHET_DESCRIPTION = (
    "Prophet — מודל Meta לסדרות עסקיות עם חגים ושינויי מגמה."
    " מטפל היטב בחריגות (מלחמה, חגים) ובנתונים חסרים."
)

def forecast_prophet(series: pd.Series, horizon: int,
                     events_df: pd.DataFrame, context: dict) -> pd.DataFrame:
    from prophet import Prophet

    months   = _future_months(series.index[-1], horizon)
    all_ev   = _extend_events(events_df, series.index[-1], horizon, context)
    ev_idx   = all_ev.drop_duplicates(subset='year_month', keep='last').set_index('year_month')

    # חישוב עמודות נגזרות אם חסרות
    if 'is_routine' not in all_ev.columns:
        all_ev['is_routine'] = (1 - (all_ev['is_war'] + all_ev['is_military_op'] + all_ev['is_ceasefire']).clip(0, 1)).astype(float)
    if 'is_black_friday' not in all_ev.columns:
        all_ev['is_black_friday'] = all_ev['year_month'].str[5:7].astype(int).eq(11).astype(float)

    # בניית DataFrame לProphet (ds = תאריך, y = ערך)
    df_train = pd.DataFrame({
        'ds': pd.to_datetime([m + "-01" for m in series.index]),
        'y':  series.values.astype(float),
    })

    # הוספת regressors מאירועים היסטוריים
    def add_regressors(df_rows, index):
        for col in ['is_war', 'is_military_op', 'is_ceasefire',
                    'jewish_holiday', 'is_summer_peak']:
            df_rows[col] = [float(index.get(
                datetime.strptime(str(d)[:7], "%Y-%m").strftime("%Y-%m"),
                {col: 0}
            ).get(col, 0)) if isinstance(index.get(
                datetime.strptime(str(d)[:7], "%Y-%m").strftime("%Y-%m")), dict
            ) else float(ev_idx.get(col, pd.Series(dtype=float)).get(
                datetime.strptime(str(d)[:7], "%Y-%m").strftime("%Y-%m"), 0))
            for d in df_rows['ds']]
        return df_rows

    # גישה פשוטה יותר — מיזוג ישיר
    df_train['ym'] = df_train['ds'].dt.strftime('%Y-%m')
    df_train = df_train.merge(
        all_ev[['year_month','is_war','is_military_op',
                'is_ceasefire','jewish_holiday','is_summer_peak',
                'is_routine','is_black_friday']],
        left_on='ym', right_on='year_month', how='left'
    ).fillna(0)

    regressor_cols = ['is_war','is_military_op','is_ceasefire',
                      'jewish_holiday','is_summer_peak',
                      'is_routine','is_black_friday']

    m = Prophet(yearly_seasonality=False, weekly_seasonality=False,
                daily_seasonality=False, interval_width=0.8,
                changepoint_prior_scale=0.05)
    for col in regressor_cols:
        m.add_regressor(col)

    m.fit(df_train[['ds','y'] + regressor_cols])

    future = pd.DataFrame({'ds': pd.to_datetime([mn + "-01" for mn in months])})
    future['ym'] = future['ds'].dt.strftime('%Y-%m')
    future = future.merge(
        all_ev[['year_month'] + regressor_cols],
        left_on='ym', right_on='year_month', how='left'
    ).fillna(0)

    fc = m.predict(future[['ds'] + regressor_cols])
    hist_cap = max(series.values) * 2
    pred      = np.clip(fc['yhat'].values,      0, hist_cap)
    pred_lo   = np.clip(fc['yhat_lower'].values, 0, hist_cap)
    pred_hi   = np.clip(fc['yhat_upper'].values, 0, hist_cap)
    return _result_df(months, pred, pred_lo, pred_hi)


# ────────────────────────────────────────────────
#  מודל 3 — XGBoost
# ────────────────────────────────────────────────
XGBOOST_DESCRIPTION = (
    "XGBoost — למידת מכונה עם פיצ'רים של עונה, מלחמה וחגים."
    " מצטיין בלכידת דפוסים לא-לינאריים ואירועי השפעה."
)

def _build_features(series: pd.Series, events_df: pd.DataFrame) -> pd.DataFrame:
    ev = events_df.drop_duplicates(subset='year_month', keep='last').set_index('year_month')
    rows = []
    yms  = list(series.index)
    for i, ym in enumerate(yms):
        y, m    = int(ym[:4]), int(ym[5:7])
        ev_row  = ev.loc[ym] if ym in ev.index else pd.Series(dtype=float)
        row = {
            'month':          m,
            'quarter':        (m - 1) // 3 + 1,
            'sin_month':      np.sin(2 * np.pi * m / 12),
            'cos_month':      np.cos(2 * np.pi * m / 12),
            'is_war':          float(ev_row.get('is_war', 0)),
            'is_military_op':  float(ev_row.get('is_military_op', 0)),
            'is_ceasefire':    float(ev_row.get('is_ceasefire', 0)),
            'jewish_holiday':  float(ev_row.get('jewish_holiday', 0)),
            'is_summer_peak':  float(ev_row.get('is_summer_peak', 0)),
            'travel_num':      _travel_impact_num(ev_row.get('travel_impact','normal')),
            'is_routine':      float(1 - min(1, float(ev_row.get('is_war',0)) + float(ev_row.get('is_military_op',0)) + float(ev_row.get('is_ceasefire',0)))),
            'is_black_friday': float(1 if m == 11 else 0),
            'lag1':           float(series.iloc[i-1]) if i > 0 else 0,
            'lag2':           float(series.iloc[i-2]) if i > 1 else 0,
            'lag3':           float(series.iloc[i-3]) if i > 2 else 0,
            'lag12':          float(series.iloc[i-12]) if i >= 12 else float(np.mean(series.values)),
            'roll3_mean':     float(np.mean(series.values[max(0,i-3):i])) if i > 0 else 0,
            'roll6_mean':     float(np.mean(series.values[max(0,i-6):i])) if i > 0 else 0,
            'target':         float(series.iloc[i]),
        }
        rows.append(row)
    return pd.DataFrame(rows)


def forecast_xgboost(series: pd.Series, horizon: int,
                     events_df: pd.DataFrame, context: dict) -> pd.DataFrame:
    from xgboost import XGBRegressor

    all_ev  = _extend_events(events_df, series.index[-1], horizon, context)
    ev_idx  = all_ev.drop_duplicates(subset='year_month', keep='last').set_index('year_month')
    months  = _future_months(series.index[-1], horizon)

    feat_cols = ['month','quarter','sin_month','cos_month',
                 'is_war','is_military_op','is_ceasefire',
                 'jewish_holiday','is_summer_peak','travel_num',
                 'is_routine','is_black_friday',
                 'lag1','lag2','lag3','lag12','roll3_mean','roll6_mean']

    df_feat = _build_features(series, all_ev)
    X_train = df_feat[feat_cols].values
    y_train = df_feat['target'].values

    model = XGBRegressor(n_estimators=200, max_depth=4, learning_rate=0.05,
                         subsample=0.8, colsample_bytree=0.8,
                         random_state=42, verbosity=0)
    model.fit(X_train, y_train)

    # חיזוי איטרטיבי — כל חודש מוסיף ל-series
    extended = list(series.values.astype(float))
    preds    = []
    for i, ym in enumerate(months):
        m = int(ym[5:7])
        n = len(extended)
        ev_row = ev_idx.loc[ym] if ym in ev_idx.index else pd.Series(dtype=float)
        row = [[
            m, (m-1)//3+1,
            np.sin(2*np.pi*m/12), np.cos(2*np.pi*m/12),
            float(ev_row.get('is_war',0)),
            float(ev_row.get('is_military_op',0)),
            float(ev_row.get('is_ceasefire',0)),
            float(ev_row.get('jewish_holiday',0)),
            float(ev_row.get('is_summer_peak',0)),
            _travel_impact_num(ev_row.get('travel_impact','normal')),
            float(1 - min(1, float(ev_row.get('is_war',0)) + float(ev_row.get('is_military_op',0)) + float(ev_row.get('is_ceasefire',0)))),
            float(1 if m == 11 else 0),
            extended[-1], extended[-2] if n>1 else 0,
            extended[-3] if n>2 else 0,
            extended[-12] if n>=12 else float(np.mean(extended)),
            float(np.mean(extended[-3:])) if n>0 else 0,
            float(np.mean(extended[-6:])) if n>0 else 0,
        ]]
        pred = float(model.predict(row)[0])
        preds.append(pred)
        extended.append(pred)

    # רווח אמון פשוט — std של שגיאות אחרונות
    train_preds = model.predict(X_train)
    residual_std = float(np.std(y_train - train_preds))
    preds_arr = np.array(preds)
    return _result_df(months,
                      preds_arr,
                      preds_arr - 1.28 * residual_std,
                      preds_arr + 1.28 * residual_std)


# ────────────────────────────────────────────────
#  מודל 4 — Newsvendor (המלצת רכש)
# ────────────────────────────────────────────────
NEWSVENDOR_DESCRIPTION = (
    "Newsvendor — מחשב כמות רכש אופטימלית תחת אי-ודאות."
    " מאזן בין עלות עודף מלאי לעלות חסר מלאי."
)

def newsvendor_order(mean_demand: float, std_demand: float,
                     gross_margin: float = 0.35,
                     holding_cost_ratio: float = 0.15) -> dict:
    """
    gross_margin      — רווח גולמי יחסי (ברירת מחדל 35%)
    holding_cost_ratio — עלות החזקת מלאי יחסית (ברירת מחדל 15%)
    """
    from scipy.stats import norm

    cu = gross_margin                      # עלות חסר (lost sale)
    co = holding_cost_ratio                # עלות עודף (holding)
    cr = cu / (cu + co)                    # critical ratio

    z        = float(norm.ppf(cr))
    optimal  = mean_demand + z * std_demand
    safety   = z * std_demand

    return {
        'mean_demand':    round(mean_demand, 1),
        'std_demand':     round(std_demand,  1),
        'critical_ratio': round(cr, 3),
        'safety_stock':   max(0, round(safety, 1)),
        'order_quantity': max(0, round(optimal, 0)),
    }


# ────────────────────────────────────────────────
#  ממשק מאחד
# ────────────────────────────────────────────────

def run_all_models(series: pd.Series, horizon: int,
                   events_df: pd.DataFrame, context: dict) -> dict:
    """
    מריץ את כל המודלים ומחזיר dict:
    {
        'arima':     DataFrame(year_month, forecast, lower, upper),
        'prophet':   DataFrame(...),
        'xgboost':   DataFrame(...),
        'newsvendor': dict עם המלצת רכש ל-horizon חודשים,
        'descriptions': {model: str},
    }
    """
    results = {}

    logger.info("run_all_models: n=%d horizon=%d context=%s", len(series), horizon, context)

    # שימוש ב-forecast_cache עוקף אימון אם הקלטים זהים לריצה קודמת.
    # אם המטמון לא זמין (למשל בייבוא ראשוני/ביצוע מבדיקות), נופל למימושים הישירים.
    try:
        from forecast_cache import cached_arima, cached_prophet, cached_xgboost
        _arima_fn   = cached_arima
        _prophet_fn = cached_prophet
        _xgboost_fn = cached_xgboost
    except Exception:
        _arima_fn, _prophet_fn, _xgboost_fn = forecast_arima, forecast_prophet, forecast_xgboost

    print("  מריץ ARIMA...")
    results['arima']   = _arima_fn(series, horizon, events_df, context)

    print("  מריץ Prophet...")
    results['prophet'] = _prophet_fn(series, horizon, events_df, context)

    print("  מריץ XGBoost...")
    results['xgboost'] = _xgboost_fn(series, horizon, events_df, context)

    # Newsvendor על ממוצע שלושת המודלים
    combined = (results['arima']['forecast'].values +
                results['prophet']['forecast'].values +
                results['xgboost']['forecast'].values) / 3.0
    results['newsvendor'] = newsvendor_order(
        mean_demand=float(combined.sum()),
        std_demand=float(np.std(series.values[-12:]) * np.sqrt(horizon)),
    )

    results['descriptions'] = {
        'arima':      ARIMA_DESCRIPTION,
        'prophet':    PROPHET_DESCRIPTION,
        'xgboost':    XGBOOST_DESCRIPTION,
        'newsvendor': NEWSVENDOR_DESCRIPTION,
    }
    return results
