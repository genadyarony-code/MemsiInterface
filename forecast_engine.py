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
#  MODEL_VERSION — נכלל ב-cache key.
#  להגדיל בכל שינוי בלוגיקת מודל, ב-features, או באלגוריתם residual std.
#  שינוי הערך הזה גורם לכל ה-cache הקיים להיחשב לא-תקף, וה-app יחשב מחדש.
#  היסטוריה:
#    "1" - גרסה התחלתית.
#    "2" - C1: הסרת is_routine מ-Prophet, פישוט features ב-XGBoost,
#          rolling residual std (12 חודשים), טעינת חגים דינמית מ-pyluach.
#    "3" - C2.5: הוספת flight_volume_lagged ו-conversion_regime כ-features
#          ל-XGBoost ו-Prophet. flight_volume normalized ל-baseline, regime
#          מקודד כ-numeric (LOW=0, MEDIUM=1, HIGH=2).
# ────────────────────────────────────────────────
MODEL_VERSION = "3"


# ────────────────────────────────────────────────
#  Cache להעשרת features מ-DB (flight_traffic + conversion_regime)
# ────────────────────────────────────────────────
_flight_cache: dict[str, float] = {}             # היסטוריה: arriving_passengers
_schedule_cache: dict[str, int] = {}              # עתיד: planned_flights (TOTAL)
_regime_cache: dict[str, str] = {}
_features_cache_loaded: bool = False


_REGIME_TO_NUM = {'LOW': 0.0, 'MEDIUM': 1.0, 'HIGH': 2.0}


def _load_features_cache() -> None:
    """טוען פעם אחת את flight_traffic + flight_schedule + conversion_regime
    מ-DB. cache בזיכרון כי הנתונים משתנים רק פעם בחודש (אחרי nightly_sync).
    """
    global _flight_cache, _schedule_cache, _regime_cache, _features_cache_loaded
    if _features_cache_loaded:
        return
    try:
        from db_config import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT year_month, arriving_passengers
                    FROM flight_traffic
                    WHERE arriving_passengers IS NOT NULL
                """)
                _flight_cache = {ym: float(v) for ym, v in cur.fetchall()}
                cur.execute("""
                    SELECT year_month, planned_flights
                    FROM flight_schedule
                    WHERE airline_code = 'TOTAL'
                """)
                _schedule_cache = {ym: int(n) for ym, n in cur.fetchall()}
                cur.execute("""
                    SELECT year_month, conversion_regime
                    FROM forecast_events
                    WHERE conversion_regime IS NOT NULL
                """)
                _regime_cache = {ym: r for ym, r in cur.fetchall()}
        _features_cache_loaded = True
        logger.info("features cache loaded: flights=%d, schedule=%d, regimes=%d",
                    len(_flight_cache), len(_schedule_cache), len(_regime_cache))
    except Exception:
        logger.exception("failed to load features cache; falling back to defaults")
        _flight_cache = {}
        _schedule_cache = {}
        _regime_cache = {}
        _features_cache_loaded = True


def invalidate_features_cache() -> None:
    """לקריאה אחרי IAA sync או שינוי ידני ב-regimes."""
    global _features_cache_loaded
    _features_cache_loaded = False


def _flight_baseline() -> float:
    """ממוצע 3 חודשים אחרונים של arriving_passengers, ל-normalization."""
    _load_features_cache()
    if not _flight_cache:
        return 700_000.0
    last_3 = sorted(_flight_cache.keys())[-3:]
    return sum(_flight_cache[k] for k in last_3) / len(last_3)


def _flight_volume_for(ym: str, fallback: float | None = None) -> float:
    """מחזיר arriving_passengers ל-year_month, מנורמלל ל-baseline.
    אם חסר — fallback ל-1.0 (כלומר baseline)."""
    _load_features_cache()
    if ym in _flight_cache:
        baseline = _flight_baseline()
        return _flight_cache[ym] / baseline if baseline > 0 else 1.0
    if fallback is not None:
        return fallback
    return 1.0


def _regime_for(ym: str, default: str = 'LOW') -> float:
    """מחזיר conversion_regime ל-year_month ככערך מספרי. ברירת-מחדל = LOW (0)."""
    _load_features_cache()
    regime = _regime_cache.get(ym, default)
    return _REGIME_TO_NUM.get(regime, 0.0)


def _prev_month(ym: str) -> str:
    """'2026-04' → '2026-03'. עוטף date arithmetic ב-helper קצר."""
    dt = datetime.strptime(ym + '-01', '%Y-%m-%d') - relativedelta(months=1)
    return dt.strftime('%Y-%m')


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

    jewish_holiday מחושב דינמית מ-pyluach לחודשים עתידיים; ה-context יכול
    לעקוף אם המשתמש סיפק ערך מפורש.
    """
    from holiday_calendar import get_jewish_holiday_months

    existing_yms = (set(events_df['year_month'].astype(str))
                    if 'year_month' in events_df.columns else set())
    rows = []
    cur = datetime.strptime(last_ym + "-01", "%Y-%m-%d")
    # חישוב חד-פעמי של חודשי-חג בטווח הרלוונטי, חוסך קריאות חוזרות.
    end = cur + relativedelta(months=horizon)
    holiday_months = get_jewish_holiday_months(cur.year, end.year)
    for _ in range(horizon):
        cur = (cur + relativedelta(months=1))
        ym  = cur.strftime("%Y-%m")
        if ym in existing_yms:
            continue  # לא לדרוס נתונים היסטוריים ב-events_df
        _w = int(context.get('is_war', 0))
        _o = int(context.get('is_military_op', 0))
        _c = int(context.get('is_ceasefire', 0))
        # ה-context יכול לעקוף, אבל ברירת המחדל היא חישוב דינמי מהלוח-העברי.
        _jh = int(context['jewish_holiday']) if 'jewish_holiday' in context \
              else (1 if ym in holiday_months else 0)
        rows.append({
            'year_month':      ym,
            'is_war':          _w,
            'is_military_op':  _o,
            'is_ceasefire':    _c,
            'jewish_holiday':  _jh,
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

    # חישוב עמודות נגזרות אם חסרות (is_routine הוסר מ-regressors ב-C1, ראה הערה למטה)
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
                'is_black_friday']],
        left_on='ym', right_on='year_month', how='left'
    ).fillna(0)

    # Sprint C2.5: flight_lag1 ו-regime כ-regressors. flight_lag1 הוא
    # ה-causal driver העיקרי (נחיתות חודש קודם → תיקונים החודש).
    # regime מקודד את הרגישות (LOW/MEDIUM/HIGH = 0/1/2).
    df_train['flight_lag1'] = df_train['ym'].apply(
        lambda y: _flight_volume_for(_prev_month(y)))
    df_train['flight_curr'] = df_train['ym'].apply(_flight_volume_for)
    df_train['regime'] = df_train['ym'].apply(_regime_for)

    # הערה (Sprint C1): is_routine הוסר. הוא היה בדיוק
    # 1 - (is_war + is_military_op + is_ceasefire), כלומר collinearity
    # מובנית עם שלושת הדגלים האחרים. Prophet היה משייט בקואפיציינטים שלא
    # ניתנים לפירוש. עכשיו רק שלושת הדגלים העצמאיים.
    regressor_cols = ['is_war','is_military_op','is_ceasefire',
                      'jewish_holiday','is_summer_peak',
                      'is_black_friday',
                      'flight_lag1','flight_curr','regime']

    m = Prophet(yearly_seasonality=False, weekly_seasonality=False,
                daily_seasonality=False, interval_width=0.8,
                changepoint_prior_scale=0.05)
    for col in regressor_cols:
        m.add_regressor(col)

    m.fit(df_train[['ds','y'] + regressor_cols])

    future = pd.DataFrame({'ds': pd.to_datetime([mn + "-01" for mn in months])})
    future['ym'] = future['ds'].dt.strftime('%Y-%m')
    # ל-future: ה-events הסטנדרטיים מוזרקים מ-all_ev, וה-flight+regime
    # מחושבים מ-DB cache עם fallback אם החודש העתידי לא קיים שם.
    future = future.merge(
        all_ev[['year_month','is_war','is_military_op','is_ceasefire',
                'jewish_holiday','is_summer_peak','is_black_friday']],
        left_on='ym', right_on='year_month', how='left'
    ).fillna(0)
    ctx_regime_num = _REGIME_TO_NUM.get(context.get('conversion_regime', 'LOW'), 0.0)
    future['flight_lag1'] = future['ym'].apply(
        lambda y: _flight_volume_for(_prev_month(y)))
    future['flight_curr'] = future['ym'].apply(_flight_volume_for)
    future['regime'] = ctx_regime_num  # לעתיד — לקחת מה-context, לא מ-DB

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
        # הערה (Sprint C1): month/quarter/is_summer_peak/is_routine הוסרו.
        # sin_month/cos_month מקודדים עונתיות חודשית באופן רציף.
        # is_summer_peak (Jul/Aug) חופף עם sin/cos. is_routine היה
        # collinear עם is_war/military_op/ceasefire.
        # Sprint C2.5: flight_volume_lag1 = נחיתות חודש קודם, מנורמללות לבייסליין.
        # הטענה הסיבתית: ביקוש לתיקונים בחודש N מונע מנחיתות בחודש N-1 (lag
        # של 2-4 שבועות בין נחיתה לתיקון). conversion_regime מקודד את הרגישות.
        prev_ym = yms[i-1] if i > 0 else ym
        flight_lag1 = _flight_volume_for(prev_ym)
        flight_curr = _flight_volume_for(ym)
        regime_num  = _regime_for(ym)

        row = {
            'sin_month':      np.sin(2 * np.pi * m / 12),
            'cos_month':      np.cos(2 * np.pi * m / 12),
            'is_war':          float(ev_row.get('is_war', 0)),
            'is_military_op':  float(ev_row.get('is_military_op', 0)),
            'is_ceasefire':    float(ev_row.get('is_ceasefire', 0)),
            'jewish_holiday':  float(ev_row.get('jewish_holiday', 0)),
            'travel_num':      _travel_impact_num(ev_row.get('travel_impact','normal')),
            'is_black_friday': float(1 if m == 11 else 0),
            'lag1':           float(series.iloc[i-1]) if i > 0 else 0,
            'lag2':           float(series.iloc[i-2]) if i > 1 else 0,
            'lag3':           float(series.iloc[i-3]) if i > 2 else 0,
            'lag12':          float(series.iloc[i-12]) if i >= 12 else float(np.mean(series.values)),
            'roll3_mean':     float(np.mean(series.values[max(0,i-3):i])) if i > 0 else 0,
            'roll6_mean':     float(np.mean(series.values[max(0,i-6):i])) if i > 0 else 0,
            'flight_lag1':    flight_lag1,
            'flight_curr':    flight_curr,
            'regime':         regime_num,
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

    # פיצ'רים אחרי C2.5: נוספו flight_lag1/flight_curr/regime.
    feat_cols = ['sin_month','cos_month',
                 'is_war','is_military_op','is_ceasefire',
                 'jewish_holiday','travel_num',
                 'is_black_friday',
                 'lag1','lag2','lag3','lag12','roll3_mean','roll6_mean',
                 'flight_lag1','flight_curr','regime']

    df_feat = _build_features(series, all_ev)
    X_train = df_feat[feat_cols].values
    y_train = df_feat['target'].values

    model = XGBRegressor(n_estimators=200, max_depth=4, learning_rate=0.05,
                         subsample=0.8, colsample_bytree=0.8,
                         random_state=42, verbosity=0)
    model.fit(X_train, y_train)

    # חיזוי איטרטיבי — כל חודש מוסיף ל-series. הסדר חייב להתאים ל-feat_cols.
    # flight features: לקחת מנתוני flight_traffic אם זמינים, אחרת fallback ל-1.0
    # (כלומר baseline). regime: נלקח מ-context אם קיים, אחרת ברירת-מחדל LOW.
    last_hist_ym = series.index[-1]
    ctx_regime = context.get('conversion_regime', 'LOW')
    ctx_regime_num = _REGIME_TO_NUM.get(ctx_regime, 0.0)

    extended = list(series.values.astype(float))
    preds    = []
    prev_ym  = last_hist_ym
    for i, ym in enumerate(months):
        m = int(ym[5:7])
        n = len(extended)
        ev_row = ev_idx.loc[ym] if ym in ev_idx.index else pd.Series(dtype=float)
        flight_lag1 = _flight_volume_for(prev_ym)
        flight_curr = _flight_volume_for(ym)
        row = [[
            np.sin(2*np.pi*m/12), np.cos(2*np.pi*m/12),
            float(ev_row.get('is_war',0)),
            float(ev_row.get('is_military_op',0)),
            float(ev_row.get('is_ceasefire',0)),
            float(ev_row.get('jewish_holiday',0)),
            _travel_impact_num(ev_row.get('travel_impact','normal')),
            float(1 if m == 11 else 0),
            extended[-1], extended[-2] if n>1 else 0,
            extended[-3] if n>2 else 0,
            extended[-12] if n>=12 else float(np.mean(extended)),
            float(np.mean(extended[-3:])) if n>0 else 0,
            float(np.mean(extended[-6:])) if n>0 else 0,
            flight_lag1, flight_curr, ctx_regime_num,
        ]]
        pred = float(model.predict(row)[0])
        preds.append(pred)
        extended.append(pred)
        prev_ym = ym

    # רווח אמון — std של שגיאות 12 חודשים אחרונים (rolling). std גלובלי
    # על כל ה-train היה מתעלם מהעובדה שעונת השוק יכולה לעבור חוסר-יציבות
    # פתאומי (מלחמה, COVID), שבו ה-noise החדש הרבה גדול יותר מההיסטוריה.
    train_preds = model.predict(X_train)
    residuals = y_train - train_preds
    recent = residuals[-12:] if len(residuals) >= 12 else residuals
    residual_std = float(np.std(recent)) if len(recent) > 0 else 0.0
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
                   events_df: pd.DataFrame, context: dict,
                   progress_callback=None) -> dict:
    """
    מריץ את כל המודלים ומחזיר dict:
    {
        'arima':     DataFrame(year_month, forecast, lower, upper),
        'prophet':   DataFrame(...),
        'xgboost':   DataFrame(...),
        'newsvendor': dict עם המלצת רכש ל-horizon חודשים,
        'descriptions': {model: str},
    }

    progress_callback: Callable[[str], None] אופציונלי. אם סופק, מקבל הודעות
    התקדמות במקום ה-print שהיה כאן בעבר. workers שמשתמשים ב-Qt signals
    צריכים לעטוף את ה-signal.emit ב-callback.
    """
    results = {}

    def _note(msg: str):
        if progress_callback is not None:
            progress_callback(msg)
        else:
            logger.info(msg)

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

    # כל מודל בנפרד עם try/except: כשל בודד לא ממוטט את כל הריצה.
    # ARIMA כבר עוטף את עצמו ב-try (fallback ל-MA(6)), אבל Prophet/XGBoost
    # היו זורקים ישר לקורא. עכשיו השלושה מטופלים אחיד.
    model_errors: dict[str, str] = {}

    def _run_model(name: str, fn):
        try:
            _note(f"  מריץ {name}...")
            return fn(series, horizon, events_df, context)
        except Exception as e:
            logger.exception("%s failed in run_all_models", name)
            model_errors[name] = f"{type(e).__name__}: {e}"
            return None

    results['arima']   = _run_model('ARIMA', _arima_fn)
    results['prophet'] = _run_model('Prophet', _prophet_fn)
    results['xgboost'] = _run_model('XGBoost', _xgboost_fn)

    # Sprint C2.8: מודל סיבתי מבוסס-נוסחה. לא תלוי ב-series; קורא ישירות
    # מ-flight_schedule/flight_traffic ומ-breakage_rate. MAPE 14.9% ב-backtest.
    # ה-slice_share מועבר מ-UI דרך context — מתאר איזה אחוז של ה-core
    # הסלייס-הנבחר מייצג. אם None, ה-causal לא רץ.
    try:
        from causal_forecast import forecast_causal
        slice_share = (context or {}).get('_causal_slice_share')
        if slice_share is not None:
            _note("  מריץ Causal...")
            results['causal'] = forecast_causal(
                series, horizon, events_df, context,
                slice_share=slice_share,
            )
        else:
            results['causal'] = None
    except Exception as e:
        logger.exception("Causal failed in run_all_models")
        model_errors['causal'] = f"{type(e).__name__}: {e}"
        results['causal'] = None

    if model_errors:
        results['model_errors'] = model_errors

    # Newsvendor — רק מהמודלים שהצליחו.
    successful = [m for m in ('arima', 'prophet', 'xgboost')
                  if results.get(m) is not None]
    if not successful:
        raise RuntimeError(f"כל המודלים נכשלו: {model_errors}")
    combined = sum(results[m]['forecast'].values for m in successful) / len(successful)
    results['newsvendor'] = newsvendor_order(
        mean_demand=float(combined.sum()),
        std_demand=float(np.std(series.values[-12:]) * np.sqrt(horizon)),
    )

    from causal_forecast import CAUSAL_DESCRIPTION
    results['descriptions'] = {
        'arima':      ARIMA_DESCRIPTION,
        'prophet':    PROPHET_DESCRIPTION,
        'xgboost':    XGBOOST_DESCRIPTION,
        'causal':     CAUSAL_DESCRIPTION,
        'newsvendor': NEWSVENDOR_DESCRIPTION,
    }
    return results
