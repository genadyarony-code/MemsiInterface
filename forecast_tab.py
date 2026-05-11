# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd
from qtpy.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QPushButton, QListWidget, QAbstractItemView, QCheckBox,
    QComboBox, QTableWidget, QTableWidgetItem, QSplitter,
    QTextEdit, QFrame, QMessageBox, QProgressBar, QTabWidget,
    QHeaderView, QSizePolicy, QScrollArea,
)
from qtpy.QtCore import Qt, QThread, Signal as pyqtSignal
from qtpy.QtGui import QFont, QColor

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from forecast_db import ForecastDB
from forecast_engine import run_all_models, forecast_arima, forecast_prophet, forecast_xgboost, newsvendor_order
from domain_repository import get_display_label
from logger import logger

try:
    from bidi.algorithm import get_display as _bidi
except ImportError:
    def _bidi(s): return s   # fallback — no reversal


def _r(text: str) -> str:
    """עברית ל-matplotlib: הפוך סדר RTL."""
    return _bidi(str(text)) if text else text


# ════════════════════════════════════════════════
#  Workers
# ════════════════════════════════════════════════
class ForecastWorker(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(dict)
    error    = pyqtSignal(str)

    def __init__(self, series, horizon, events_df, context,
                 branches=None, categories=None, persist=True):
        super().__init__()
        self.series = series; self.horizon = horizon
        self.events_df = events_df; self.context = context
        self.branches = branches or []
        self.categories = categories or []
        self.persist = persist

    def run(self):
        try:
            res = run_all_models(
                self.series, self.horizon, self.events_df, self.context,
                progress_callback=self.progress.emit,
            )

            # Backtest על 6 חודשים אחרונים (אם יש מספיק נתונים)
            metrics = {}
            try:
                from forecast_evaluation import backtest, save_run
                self.progress.emit("מחשב אמינות מודלים על היסטוריה…")
                metrics = backtest(self.series, self.events_df, self.context,
                                   test_size=6)
                res['metrics'] = metrics

                if self.persist:
                    self.progress.emit("שומר ריצה ב-DB…")
                    run_id = save_run(
                        branches=self.branches,
                        categories=self.categories,
                        horizon_months=self.horizon,
                        context=self.context,
                        series_n=len(self.series),
                        results=res,
                        metrics=metrics,
                    )
                    res['run_id'] = run_id
            except Exception as ev_err:
                logger.exception("forecast evaluation/persistence failed")
                # ממשיכים גם אם backtest/save נכשלו, לא לחסום את התחזית עצמה.
                res.setdefault('metrics', {})

            self.finished.emit(res)
        except Exception:
            import traceback
            tb = traceback.format_exc()
            logger.exception("ForecastWorker failed: n=%d horizon=%d ctx=%s",
                             len(self.series), self.horizon, self.context)
            self.error.emit(tb)


class ProcurementWorker(QThread):
    """מריץ ARIMA + XGBoost לכל קטגוריה בנפרד."""
    progress = pyqtSignal(str)
    finished = pyqtSignal(object)
    error    = pyqtSignal(str)

    def __init__(self, hist_df, events_df, context):
        super().__init__()
        self.hist_df = hist_df; self.events_df = events_df; self.context = context

    def run(self):
        try:
            out  = {}
            cats = sorted(self.hist_df['luggage_type'].unique())
            n    = len(cats)
            for i, cat in enumerate(cats):
                self.progress.emit(f"[{i+1}/{n}] {cat}…")
                sub = self.hist_df[self.hist_df['luggage_type'] == cat]
                agg = sub.groupby('year_month')['quantity'].sum().sort_index()
                s   = pd.Series(agg.values, index=agg.index)
                if len(s) < 3:
                    continue
                ar6  = forecast_arima(s, 6, self.events_df, self.context)
                xg6  = forecast_xgboost(s, 6, self.events_df, self.context)
                nv   = newsvendor_order(
                    mean_demand=float((ar6['forecast'].sum() + xg6['forecast'].sum()) / 2),
                    std_demand=float(s.std() * np.sqrt(6)),
                )
                out[cat] = {
                    'arima': ar6, 'xgboost': xg6,
                    'newsvendor': nv,
                    'hist_avg3': round(float(s.tail(3).mean()), 1),
                }
            self.finished.emit(out)
        except Exception:
            import traceback
            tb = traceback.format_exc()
            logger.exception("ProcurementWorker failed: rows=%d ctx=%s",
                             len(self.hist_df), self.context)
            self.error.emit(tb)


# ════════════════════════════════════════════════
#  Chart helpers
# ════════════════════════════════════════════════
def _new_fig(h=3.8):
    fig = Figure(figsize=(9, h), facecolor='#fafafa')
    fig.subplots_adjust(left=0.07, right=0.97, top=0.92, bottom=0.18)
    return fig


def _style_ax(ax):
    ax.set_facecolor('#f8f9fa')
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.grid(axis='y', linestyle=':', alpha=0.5, color='#ccc')


def _canvas(fig):
    c = FigureCanvas(fig)
    c.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    return c


# ════════════════════════════════════════════════
#  Tab 1 — גרף תחזית ראשי
# ════════════════════════════════════════════════
class ForecastChart(QWidget):
    def __init__(self):
        super().__init__()
        self.fig = _new_fig(4.0)
        self.cv  = _canvas(self.fig)
        lay = QVBoxLayout(self); lay.setContentsMargins(0,0,0,0)
        lay.addWidget(self.cv)
        self._draw_placeholder()

    def _draw_placeholder(self):
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        ax.text(0.5, 0.5, 'Select branches & categories then click Run',
                ha='center', va='center', color='#aaa', fontsize=11,
                transform=ax.transAxes)
        ax.axis('off')
        self.cv.draw()

    def plot(self, history: pd.Series, results: dict):
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        _style_ax(ax)

        hx = list(range(len(history)))
        ax.plot(hx, history.values, color='#2c3e50', lw=2.5,
                label='History', zorder=5)
        ax.axvline(x=hx[-1], color='#aaa', ls='--', lw=1, alpha=0.7)

        MODEL_CFG = [
            ('arima',   '#3498db', 'ARIMA'),
            ('prophet', '#27ae60', 'Prophet'),
            ('xgboost', '#e67e22', 'XGBoost'),
        ]
        base = len(history)
        for key, col, lbl in MODEL_CFG:
            if key not in results: continue
            df = results[key]
            fx = list(range(base, base + len(df)))
            cx = [hx[-1]] + fx
            cy = [float(history.values[-1])] + df['forecast'].tolist()
            ax.plot(cx, cy, color=col, lw=2, ls='--', label=lbl, alpha=0.9)
            ax.fill_between(fx, df['lower'], df['upper'], color=col, alpha=0.10)
            ax.scatter(fx, df['forecast'], color=col, s=28, zorder=6, alpha=0.85)

        all_m = list(history.index) + (
            results['arima']['year_month'].tolist() if 'arima' in results else [])
        step  = max(1, len(all_m) // 14)
        ax.set_xticks(range(0, len(all_m), step))
        ax.set_xticklabels([all_m[i] for i in range(0, len(all_m), step)],
                           rotation=38, ha='right', fontsize=8)
        ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda v,_: f'{int(v):,}'))
        ax.legend(fontsize=9, loc='upper left', framealpha=0.85, edgecolor='#ddd')
        self.fig.tight_layout(pad=1.2)
        self.cv.draw()


# ════════════════════════════════════════════════
#  Tab 2 / 3 — עמודות התפלגות
# ════════════════════════════════════════════════
class DistChart(QWidget):
    def __init__(self):
        super().__init__()
        self.fig = _new_fig(3.8)
        self.cv  = _canvas(self.fig)
        lay = QVBoxLayout(self); lay.setContentsMargins(0,0,0,0)
        lay.addWidget(self.cv)

    def plot(self, data: pd.Series, xlabel: str, color: str, title: str):
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        _style_ax(ax)
        y = range(len(data))
        ax.barh(list(y), data.values, color=color, alpha=0.8)
        labels = [_r(str(lbl)) for lbl in data.index]
        ax.set_yticks(list(y))
        ax.set_yticklabels(labels, fontsize=8)
        ax.set_xlabel(xlabel, fontsize=9)
        ax.set_title(_r(title), fontsize=10, color='#2c3e50', fontweight='bold')
        ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda v,_: f'{int(v):,}'))
        self.fig.tight_layout(pad=1.2)
        self.cv.draw()


# ════════════════════════════════════════════════
#  Tab 4 — עונתיות Heatmap
# ════════════════════════════════════════════════
class SeasonalChart(QWidget):
    MONTH_HEB = ['ינו','פבר','מרץ','אפר','מאי','יונ',
                 'יול','אוג','ספט','אוק','נוב','דצמ']

    def __init__(self):
        super().__init__()
        self.fig = _new_fig(3.8)
        self.cv  = _canvas(self.fig)
        lay = QVBoxLayout(self); lay.setContentsMargins(0,0,0,0)
        lay.addWidget(self.cv)

    def plot(self, hist_df: pd.DataFrame, title: str):
        self.fig.clear()
        ax   = self.fig.add_subplot(111)
        agg  = hist_df.groupby('year_month')['quantity'].sum().reset_index()
        agg['year']  = agg['year_month'].str[:4].astype(int)
        agg['month'] = agg['year_month'].str[5:7].astype(int)
        pivot = agg.pivot_table(index='year', columns='month',
                                values='quantity', aggfunc='sum').fillna(0)
        im = ax.imshow(pivot.values, aspect='auto', cmap='YlOrRd',
                       interpolation='nearest')
        ax.set_xticks(range(len(pivot.columns)))
        ax.set_xticklabels([self.MONTH_HEB[m-1] for m in pivot.columns], fontsize=9)
        ax.set_yticks(range(len(pivot.index)))
        ax.set_yticklabels(pivot.index.astype(str), fontsize=9)
        self.fig.colorbar(im, ax=ax, shrink=0.8, label='Units')
        # label each cell
        vmax = pivot.values.max()
        for r, yr in enumerate(pivot.index):
            for c, mo in enumerate(pivot.columns):
                v = int(pivot.loc[yr, mo])
                ax.text(c, r, str(v) if v else '',
                        ha='center', va='center', fontsize=7,
                        color='white' if v > 0.6*vmax else '#333')
        ax.set_title(_r(title), fontsize=10, color='#2c3e50', fontweight='bold')
        self.fig.tight_layout(pad=1.2)
        self.cv.draw()


# ════════════════════════════════════════════════
#  Worker — תמונת מצב סניף
# ════════════════════════════════════════════════
class BranchSnapshotWorker(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(dict)
    error    = pyqtSignal(str)

    def __init__(self, series_total, hist_df, events_df, context):
        super().__init__()
        self.series_total = series_total
        self.hist_df      = hist_df
        self.events_df    = events_df
        self.context      = context

    def run(self):
        try:
            out = {}
            self.progress.emit("מריץ מודלים כלליים…")
            out['total'] = run_all_models(
                self.series_total, 6, self.events_df, self.context,
                progress_callback=self.progress.emit,
            )

            by_cat = {}
            cats = sorted(self.hist_df['luggage_type'].unique())
            for i, cat in enumerate(cats):
                self.progress.emit(f"[{i+1}/{len(cats)}] {cat}…")
                sub = self.hist_df[self.hist_df['luggage_type'] == cat]
                agg = sub.groupby('year_month')['quantity'].sum().sort_index()
                s   = pd.Series(agg.values, index=agg.index)
                if len(s) < 3:
                    continue
                ar6 = forecast_arima(s, 6, self.events_df, self.context)
                xg6 = forecast_xgboost(s, 6, self.events_df, self.context)
                avg = ar6.copy()
                avg['forecast'] = ((ar6['forecast'] + xg6['forecast']) / 2).round().astype(int)
                by_cat[cat] = {'arima': ar6, 'xgboost': xg6, 'avg': avg}
            out['by_cat'] = by_cat

            self.finished.emit(out)
        except Exception:
            import traceback
            tb = traceback.format_exc()
            logger.exception("BranchSnapshotWorker failed: n_total=%d rows=%d ctx=%s",
                             len(self.series_total), len(self.hist_df), self.context)
            self.error.emit(tb)


# ════════════════════════════════════════════════
#  Chart — תחזית לפי קטגוריה (עמודות מוערמות)
# ════════════════════════════════════════════════
class CategoryForecastChart(QWidget):
    COLORS = ['#3498db','#27ae60','#e67e22','#9b59b6','#e74c3c',
              '#1abc9c','#f39c12','#2980b9','#8e44ad','#16a085']

    def __init__(self):
        super().__init__()
        self.fig = Figure(figsize=(10, 5.5), facecolor='#fafafa')
        self.fig.subplots_adjust(left=0.07, right=0.70, top=0.90, bottom=0.18)
        self.cv  = _canvas(self.fig)
        lay = QVBoxLayout(self); lay.setContentsMargins(0,0,0,0)
        lay.addWidget(self.cv)

    def plot(self, by_cat: dict, title: str):
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        _style_ax(ax)
        if not by_cat:
            ax.text(0.5, 0.5, 'No data', ha='center', va='center',
                    color='#aaa', transform=ax.transAxes); ax.axis('off')
            self.cv.draw(); return

        cats   = list(by_cat.keys())
        months = by_cat[cats[0]]['avg']['year_month'].tolist()
        x      = np.arange(len(months))
        bottom = np.zeros(len(months))

        for i, cat in enumerate(cats):
            vals = by_cat[cat]['avg']['forecast'].values.astype(float)
            col  = self.COLORS[i % len(self.COLORS)]
            ax.bar(x, vals, 0.65, bottom=bottom, color=col,
                   label=_r(cat), alpha=0.88, edgecolor='white', linewidth=0.4)
            # ערך בתוך עמודה אם מספיק גדולה
            for j, v in enumerate(vals):
                if v > bottom.max() * 0.05 + 1:
                    ax.text(x[j], bottom[j] + v / 2, str(int(v)),
                            ha='center', va='center', fontsize=7, color='white', fontweight='bold')
            bottom += vals

        ax.set_xticks(x)
        ax.set_xticklabels(months, rotation=38, ha='right', fontsize=8)
        ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f'{int(v):,}'))
        ax.legend(fontsize=8, loc='upper left', framealpha=0.85,
                  bbox_to_anchor=(1.01, 1), borderaxespad=0, ncol=1)
        ax.set_title(_r(title), fontsize=10, color='#2c3e50', fontweight='bold')
        self.cv.draw()


# ════════════════════════════════════════════════
#  לשונית תחזיות ראשית
# ════════════════════════════════════════════════
class ForecastTab(QWidget):
    def __init__(self):
        super().__init__()
        self.fdb              = None
        self._branch_code_map = {}
        self._hist_df         = pd.DataFrame()
        self._series          = pd.Series(dtype=float)
        self._results         = {}
        self._init_ui()
        self._load_controls()

    # ────────────────────────────────────────────
    #  UI
    # ────────────────────────────────────────────
    def _init_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        # ── פאנל שמאל (קבוע) ───────────────────
        left = QWidget(); left.setFixedWidth(265)
        lv   = QVBoxLayout(left); lv.setSpacing(5); lv.setContentsMargins(0,0,0,0)

        def _grp(title, widget, btn_label, btn_slot):
            gb  = QGroupBox(title)
            gb.setStyleSheet("QGroupBox{font-weight:bold;font-size:12px;}")
            gv  = QVBoxLayout(gb); gv.setSpacing(3)
            widget.setFixedHeight(130)
            widget.setStyleSheet(
                "QListWidget{font-size:12px;}"
                "QListWidget::item:selected{background:#2980b9;color:white;}")
            gv.addWidget(widget)
            btn = QPushButton(btn_label); btn.setFixedHeight(22)
            btn.clicked.connect(btn_slot); gv.addWidget(btn)
            return gb

        self.branch_list   = QListWidget()
        self.branch_list.setSelectionMode(QAbstractItemView.MultiSelection)
        self.category_list = QListWidget()
        self.category_list.setSelectionMode(QAbstractItemView.MultiSelection)
        self.category_list.setStyleSheet(
            "QListWidget{font-size:12px;}"
            "QListWidget::item:selected{background:#27ae60;color:white;}")

        lv.addWidget(_grp("סניפים",         self.branch_list,   "בחר הכל", self.branch_list.selectAll))
        lv.addWidget(_grp("קטגוריית מוצר",  self.category_list, "בחר הכל", self.category_list.selectAll))

        # אופק
        gb_h = QGroupBox("אופק תחזית")
        gh   = QVBoxLayout(gb_h)
        self.horizon_combo = QComboBox()
        self.horizon_combo.addItems(["חודש הבא (1)","3 חודשים","6 חודשים","9 חודשים","12 חודשים"])
        self.horizon_combo.setCurrentIndex(2)
        gh.addWidget(self.horizon_combo); lv.addWidget(gb_h)

        # קונטקסט
        gb_c = QGroupBox("קונטקסט נוכחי")
        gc   = QVBoxLayout(gb_c); gc.setSpacing(2)
        self.cb_war    = QCheckBox("מלחמה פעילה")
        self.cb_op     = QCheckBox("מבצע צבאי")
        self.cb_cease  = QCheckBox("הפסקת אש"); self.cb_cease.setChecked(True)
        self.cb_passov = QCheckBox("פסח (עונת שיא)")
        self.cb_highh  = QCheckBox('חגי תשרי (ר"ה / סוכות)')
        self.cb_summer = QCheckBox("קיץ (יולי–אוגוסט)")
        self.cb_bf     = QCheckBox("נובמבר / בלאק פריידי")
        sep = QFrame(); sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color:#ddd;")
        routine_lbl = QLabel("(ללא סימון = שגרה רגילה)")
        routine_lbl.setStyleSheet("font-size:10px;color:#888;")
        for cb in [self.cb_war,self.cb_op,self.cb_cease,
                   self.cb_passov,self.cb_highh,self.cb_summer,self.cb_bf]:
            gc.addWidget(cb)
        gc.addWidget(sep)
        gc.addWidget(routine_lbl)
        lv.addWidget(gb_c)

        # כפתורי הרצה
        self.run_btn = QPushButton("הרץ תחזית")
        self.run_btn.setFixedHeight(38)
        self.run_btn.setStyleSheet(
            "QPushButton{background:#2980b9;color:white;font-size:14px;"
            "font-weight:bold;border-radius:5px;}"
            "QPushButton:hover{background:#1f6891;}"
            "QPushButton:disabled{background:#bdc3c7;}")
        self.run_btn.clicked.connect(self._run_forecast)
        lv.addWidget(self.run_btn)

        self.proc_btn = QPushButton("חשב תכנון רכש")
        self.proc_btn.setFixedHeight(30)
        self.proc_btn.setStyleSheet(
            "QPushButton{background:#8e44ad;color:white;font-size:12px;"
            "border-radius:5px;}"
            "QPushButton:hover{background:#6c3483;}"
            "QPushButton:disabled{background:#bdc3c7;}")
        self.proc_btn.clicked.connect(self._run_procurement)
        lv.addWidget(self.proc_btn)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0,0); self.progress_bar.setFixedHeight(5)
        self.progress_bar.setVisible(False); lv.addWidget(self.progress_bar)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("font-size:11px;color:#666;")
        lv.addWidget(self.status_label)
        lv.addStretch()
        root.addWidget(left)

        # ── פאנל ימין — לשוניות משנה ────────────
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(
            "QTabBar::tab{font-size:12px;padding:5px 14px;}"
            "QTabBar::tab:selected{font-weight:bold;color:#2980b9;}")

        # Tab 1 — תחזית
        self.tabs.addTab(self._build_tab_forecast(),    "תחזית")
        # Tab 2 — לפי קטגוריה
        self.tabs.addTab(self._build_tab_by_category(), "לפי קטגוריה")
        # Tab 3 — לפי סניף
        self.tabs.addTab(self._build_tab_by_branch(),   "לפי סניף")
        # Tab 4 — עונתיות
        self.tabs.addTab(self._build_tab_seasonal(),    "עונתיות")
        # Tab 5 — תכנון רכש
        self.tabs.addTab(self._build_tab_procurement(), "תכנון רכש")
        # Tab 6 — תמונת מצב סניף
        self.tabs.addTab(self._build_tab_snapshot(), "תמונת מצב סניף")

        root.addWidget(self.tabs)

    # ── Tab 1: תחזית ────────────────────────────
    def _build_tab_forecast(self):
        w  = QWidget(); v = QVBoxLayout(w); v.setSpacing(5)
        self.fc_title = QLabel("בחר סניפים וקטגוריות ולחץ הרץ תחזית")
        self.fc_title.setAlignment(Qt.AlignCenter)
        self.fc_title.setStyleSheet(
            "font-size:13px;font-weight:bold;color:#2c3e50;"
            "padding:3px;background:#eaf4fb;border-radius:4px;")
        v.addWidget(self.fc_title)

        self.fc_chart = ForecastChart()
        v.addWidget(self.fc_chart)

        self.fc_table = QTableWidget(0, 7)
        self.fc_table.setHorizontalHeaderLabels(
            ["חודש","ARIMA","Prophet","XGBoost","ממוצע","טווח","שינוי %"])
        self.fc_table.setMaximumHeight(165)
        self.fc_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.fc_table.setAlternatingRowColors(True)
        self.fc_table.horizontalHeader().setStretchLastSection(True)
        self.fc_table.setStyleSheet(
            "QTableWidget{font-size:12px;}"
            "QHeaderView::section{font-weight:bold;padding:3px;"
            "background:#2c3e50;color:white;}")
        v.addWidget(self.fc_table)

        # Newsvendor bar
        nv_frame = QFrame()
        nv_frame.setStyleSheet(
            "QFrame{background:#f0f8ff;border:1px solid #bee3f8;border-radius:6px;}")
        nh = QHBoxLayout(nv_frame); nh.setContentsMargins(10,6,10,6)
        self.nv_vals = {}
        for key, lbl, col in [
            ('mean_demand',   'ביקוש ממוצע לתקופה', '#2c3e50'),
            ('safety_stock',  'מלאי בטחון',          '#e67e22'),
            ('order_quantity','מומלץ להזמין',         '#27ae60'),
        ]:
            bx = QVBoxLayout()
            tl = QLabel(lbl); tl.setAlignment(Qt.AlignCenter)
            tl.setStyleSheet("font-size:10px;color:#888;")
            vl = QLabel("—");  vl.setAlignment(Qt.AlignCenter)
            vl.setStyleSheet(f"font-size:22px;font-weight:bold;color:{col};")
            bx.addWidget(tl); bx.addWidget(vl)
            nh.addLayout(bx)
            self.nv_vals[key] = vl
            if key != 'order_quantity':
                sep = QFrame(); sep.setFrameShape(QFrame.VLine)
                sep.setStyleSheet("color:#bee3f8;"); nh.addWidget(sep)
        v.addWidget(nv_frame)

        self.fc_desc = QTextEdit()
        self.fc_desc.setReadOnly(True); self.fc_desc.setMaximumHeight(65)
        self.fc_desc.setStyleSheet("font-size:11px;background:#fafafa;border:none;color:#555;")
        v.addWidget(self.fc_desc)
        return w

    # ── Tab 2: לפי קטגוריה ──────────────────────
    def _build_tab_by_category(self):
        w = QWidget(); v = QVBoxLayout(w); v.setSpacing(4)
        lbl = QLabel("ממוצע חודשי (3 חודשים אחרונים) לפי קטגוריית מוצר — לסניפים שנבחרו")
        lbl.setStyleSheet("font-size:11px;color:#555;padding:2px;")
        v.addWidget(lbl)
        self.cat_chart = DistChart()
        v.addWidget(self.cat_chart)
        self.cat_table = QTableWidget(0, 3)
        self.cat_table.setHorizontalHeaderLabels(["קטגוריה","ממוצע 3M","% מסה\"כ"])
        self.cat_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.cat_table.setAlternatingRowColors(True)
        self.cat_table.horizontalHeader().setStretchLastSection(True)
        self.cat_table.setStyleSheet(
            "QTableWidget{font-size:12px;}"
            "QHeaderView::section{font-weight:bold;background:#27ae60;color:white;padding:3px;}")
        v.addWidget(self.cat_table)
        return w

    # ── Tab 3: לפי סניף ─────────────────────────
    def _build_tab_by_branch(self):
        w = QWidget(); v = QVBoxLayout(w); v.setSpacing(4)
        lbl = QLabel("ממוצע חודשי (3 חודשים אחרונים) לפי סניף — לקטגוריות שנבחרו")
        lbl.setStyleSheet("font-size:11px;color:#555;padding:2px;")
        v.addWidget(lbl)
        self.br_chart = DistChart()
        v.addWidget(self.br_chart)
        self.br_table = QTableWidget(0, 3)
        self.br_table.setHorizontalHeaderLabels(["סניף","ממוצע 3M","% מסה\"כ"])
        self.br_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.br_table.setAlternatingRowColors(True)
        self.br_table.horizontalHeader().setStretchLastSection(True)
        self.br_table.setStyleSheet(
            "QTableWidget{font-size:12px;}"
            "QHeaderView::section{font-weight:bold;background:#3498db;color:white;padding:3px;}")
        v.addWidget(self.br_table)
        return w

    # ── Tab 4: עונתיות ───────────────────────────
    def _build_tab_seasonal(self):
        w = QWidget(); v = QVBoxLayout(w); v.setSpacing(4)
        lbl = QLabel("עוצמת ביקוש לפי חודש ושנה (Heatmap) — לבחירה הנוכחית")
        lbl.setStyleSheet("font-size:11px;color:#555;padding:2px;")
        v.addWidget(lbl)
        self.sea_chart = SeasonalChart()
        v.addWidget(self.sea_chart)
        return w

    # ── Tab 5: תכנון רכש ────────────────────────
    def _build_tab_procurement(self):
        w  = QWidget(); v = QVBoxLayout(w); v.setSpacing(5)

        top = QHBoxLayout()
        top.addWidget(QLabel("אופק:"))
        self.proc_horizon = QComboBox()
        self.proc_horizon.addItems(["חודש הבא","3 חודשים","6 חודשים"])
        self.proc_horizon.currentIndexChanged.connect(self._refresh_procurement_table)
        top.addWidget(self.proc_horizon)
        top.addStretch()
        info = QLabel("ARIMA + XGBoost לכל קטגוריה בנפרד | לחץ 'חשב תכנון רכש' להפעלה")
        info.setStyleSheet("font-size:11px;color:#888;")
        top.addWidget(info)
        v.addLayout(top)

        self.proc_table = QTableWidget(0, 7)
        self.proc_table.setHorizontalHeaderLabels([
            "קטגוריה","ממוצע היסט.",
            "ARIMA","XGBoost","ממוצע מודלים",
            "מלאי בטחון","מומלץ להזמין"])
        self.proc_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.proc_table.setAlternatingRowColors(True)
        self.proc_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.proc_table.setStyleSheet(
            "QTableWidget{font-size:12px;}"
            "QHeaderView::section{font-weight:bold;background:#8e44ad;color:white;padding:3px;}")
        v.addWidget(self.proc_table)
        self._proc_results = {}
        return w

    # ── Tab 6: תמונת מצב סניף ───────────────────
    def _build_tab_snapshot(self):
        outer = QWidget()
        ov    = QVBoxLayout(outer); ov.setSpacing(5); ov.setContentsMargins(4,4,4,4)

        # שורת בחירת סניף
        top = QHBoxLayout()
        top.addWidget(QLabel("סניף:"))
        self.snap_combo = QComboBox()
        self.snap_combo.setMinimumWidth(220)
        top.addWidget(self.snap_combo)
        self.snap_btn = QPushButton("טען תמונת מצב")
        self.snap_btn.setFixedHeight(30)
        self.snap_btn.setStyleSheet(
            "QPushButton{background:#16a085;color:white;font-size:12px;"
            "font-weight:bold;border-radius:5px;}"
            "QPushButton:hover{background:#1abc9c;}"
            "QPushButton:disabled{background:#bdc3c7;}")
        self.snap_btn.clicked.connect(self._load_snapshot)
        top.addWidget(self.snap_btn)
        self.snap_status = QLabel("")
        self.snap_status.setStyleSheet("font-size:11px;color:#666;")
        top.addWidget(self.snap_status, 1)
        ov.addLayout(top)

        # ScrollArea לכל התוכן — גלילה חופשית
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        content = QWidget()
        cv = QVBoxLayout(content); cv.setSpacing(10)

        # ── 6 חודשים אחרונים (pivot) ─────────────
        sec1 = QGroupBox("6 חודשים אחרונים — מכירות לפי זיהוי")
        sec1.setStyleSheet("QGroupBox{font-weight:bold;font-size:12px;color:#2c3e50;}")
        s1v = QVBoxLayout(sec1)
        self.snap_hist_table = QTableWidget(0, 0)
        self.snap_hist_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.snap_hist_table.setAlternatingRowColors(True)
        self.snap_hist_table.setMinimumHeight(210)
        self.snap_hist_table.setMaximumHeight(320)
        self.snap_hist_table.setStyleSheet(
            "QTableWidget{font-size:12px;}"
            "QHeaderView::section{font-weight:bold;background:#2c3e50;color:white;padding:3px;}")
        s1v.addWidget(self.snap_hist_table)
        cv.addWidget(sec1)

        # ── תחזית כוללת 3 מודלים ─────────────────
        sec2 = QGroupBox("תחזית כוללת — 3 מודלים (6 חודשים קדימה)")
        sec2.setStyleSheet("QGroupBox{font-weight:bold;font-size:12px;color:#2c3e50;}")
        s2v = QVBoxLayout(sec2)
        self.snap_fc_chart = ForecastChart()
        self.snap_fc_chart.setMinimumHeight(320)
        self.snap_fc_table = QTableWidget(0, 7)
        self.snap_fc_table.setHorizontalHeaderLabels(
            ["חודש","ARIMA","Prophet","XGBoost","ממוצע","טווח","שינוי %"])
        self.snap_fc_table.setMinimumHeight(185)
        self.snap_fc_table.setMaximumHeight(230)
        self.snap_fc_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.snap_fc_table.setAlternatingRowColors(True)
        self.snap_fc_table.horizontalHeader().setStretchLastSection(True)
        self.snap_fc_table.setStyleSheet(
            "QTableWidget{font-size:12px;}"
            "QHeaderView::section{font-weight:bold;background:#2c3e50;color:white;padding:3px;}")
        s2v.addWidget(self.snap_fc_chart)
        s2v.addWidget(self.snap_fc_table)
        cv.addWidget(sec2)

        # ── תחזית לפי זיהוי ─────────────────────
        sec3 = QGroupBox("תחזית לפי זיהוי מוצר — ממוצע ARIMA+XGBoost")
        sec3.setStyleSheet("QGroupBox{font-weight:bold;font-size:12px;color:#2c3e50;}")
        s3v = QVBoxLayout(sec3)

        # טבלה בשלמותה — כל השורות גלויות
        self.snap_cat_table = QTableWidget(0, 7)
        self.snap_cat_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.snap_cat_table.setAlternatingRowColors(True)
        self.snap_cat_table.setMinimumHeight(220)
        self.snap_cat_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.snap_cat_table.setStyleSheet(
            "QTableWidget{font-size:11px;}"
            "QHeaderView::section{font-weight:bold;background:#16a085;color:white;padding:3px;}")
        s3v.addWidget(self.snap_cat_table)

        # גרף עמודות מוערמות מתחת לטבלה — גבוה
        self.snap_cat_chart = CategoryForecastChart()
        self.snap_cat_chart.setMinimumHeight(420)
        s3v.addWidget(self.snap_cat_chart)

        cv.addWidget(sec3)

        content.setLayout(cv)
        scroll.setWidget(content)
        ov.addWidget(scroll)
        self._snap_results = {}
        return outer

    # ────────────────────────────────────────────
    #  טעינת נתונים לפקדים
    # ────────────────────────────────────────────
    def _load_controls(self):
        try:
            self.fdb = ForecastDB()
            self.fdb.setup_tables()
            active = self.fdb.get_active_branches(inactive_months=5)
            self._branch_code_map = {}
            self.branch_list.clear()
            self.snap_combo.clear()
            for code in active:
                label = get_display_label(code)
                self._branch_code_map[label] = code
                self.branch_list.addItem(label)
                self.snap_combo.addItem(label)
            hist = self.fdb.get_history()
            cats = (hist.groupby('luggage_type')['quantity']
                    .sum().sort_values(ascending=False).index.tolist())
            self.category_list.clear()
            for c in cats:
                self.category_list.addItem(c)
            total = len(self.fdb.get_branches())
            self.status_label.setText(f"{len(active)} פעילים מתוך {total}")
        except Exception as e:
            self.status_label.setText(f"שגיאת DB: {e}")

    # ────────────────────────────────────────────
    #  עזרים
    # ────────────────────────────────────────────
    def _get_horizon(self) -> int:
        return {"חודש הבא (1)":1,"3 חודשים":3,"6 חודשים":6,
                "9 חודשים":9,"12 חודשים":12}.get(
            self.horizon_combo.currentText(), 6)

    def _build_context(self) -> dict:
        ctx = {
            'is_war':          int(self.cb_war.isChecked()),
            'is_military_op':  int(self.cb_op.isChecked()),
            'is_ceasefire':    int(self.cb_cease.isChecked()),
            'is_summer_peak':  int(self.cb_summer.isChecked()),
            'is_black_friday': int(self.cb_bf.isChecked()),
            'jewish_holiday':  1 if self.cb_passov.isChecked() else
                               2 if self.cb_highh.isChecked()  else 0,
        }
        ctx['is_routine'] = int(not (ctx['is_war'] or ctx['is_military_op'] or ctx['is_ceasefire']))
        ctx['travel_impact'] = (
            'very_low' if ctx['is_war'] else
            'low'      if ctx['is_military_op'] else
            'high'     if (ctx['is_summer_peak'] or ctx['jewish_holiday']) else 'normal')
        return ctx

    def _make_title(self, labels, cats, horizon) -> str:
        b = labels[0] if len(labels)==1 else f"{len(labels)} סניפים"
        c = cats[0]   if len(cats)==1   else \
            "כל הקטגוריות" if len(cats)>=20 else f"{len(cats)} קטגוריות"
        h = {1:"חודש הבא",3:"3 חודשים",6:"6 חודשים",12:"שנה"}.get(horizon,f"{horizon}M")
        return f"תחזית {h}  ·  {b}  ·  {c}"

    def _sel_codes(self):
        return [self._branch_code_map.get(i.text(), i.text())
                for i in self.branch_list.selectedItems()]

    def _sel_cats(self):
        return [i.text() for i in self.category_list.selectedItems()]

    # ────────────────────────────────────────────
    #  תמונת מצב סניף (Tab 6)
    # ────────────────────────────────────────────
    def _load_snapshot(self):
        label = self.snap_combo.currentText()
        if not label:
            return
        code = self._branch_code_map.get(label, label)
        hist = self.fdb.get_history(branches=[code])
        if hist.empty:
            QMessageBox.warning(self, "נתונים", "אין היסטוריה לסניף זה"); return

        # pivot: 6 חודשים אחרונים
        last6 = sorted(hist['year_month'].unique())[-6:]
        hist6 = hist[hist['year_month'].isin(last6)]
        pivot = (hist6.groupby(['luggage_type','year_month'])['quantity']
                 .sum().unstack(fill_value=0))
        pivot = pivot.reindex(columns=last6, fill_value=0)
        self._fill_snap_hist_table(pivot)

        # series כוללת
        agg = hist.groupby('year_month')['quantity'].sum().sort_index()
        series = pd.Series(agg.values, index=agg.index)

        self.snap_btn.setEnabled(False)
        self.snap_status.setText("מריץ מודלים…")
        self.snap_fc_chart._draw_placeholder()

        self._snap_worker = BranchSnapshotWorker(
            series, hist, self.fdb.get_events(), self._build_context())
        self._snap_worker.progress.connect(self.snap_status.setText)
        self._snap_worker.finished.connect(lambda r: self._on_snapshot_done(r, series, label))
        self._snap_worker.error.connect(self._on_snap_error)
        self._snap_worker.start()

    def _on_snapshot_done(self, results, series, branch_label):
        self._snap_results = results
        self.snap_btn.setEnabled(True)
        self.snap_status.setText(f"עודכן ✓  ·  {branch_label}")

        # גרף תחזית כוללת
        self.snap_fc_chart.plot(series, results['total'])
        # טבלת תחזית כוללת (שימוש חוזר ב-_fill_fc_table logic)
        self._fill_snap_fc_table(results['total'], series)
        # גרף + טבלת לפי קטגוריה
        by_cat = results.get('by_cat', {})
        self._fill_snap_cat_table(by_cat)
        self.snap_cat_chart.plot(by_cat, f"תחזית לפי זיהוי — {branch_label}")

    def _on_snap_error(self, tb):
        self.snap_btn.setEnabled(True)
        self.snap_status.setText("שגיאה")
        QMessageBox.critical(self, "שגיאה", tb[:1000])

    def _fill_snap_hist_table(self, pivot: pd.DataFrame):
        months = list(pivot.columns)
        t = self.snap_hist_table
        t.setColumnCount(len(months) + 2)
        headers = ["זיהוי מוצר"] + months + ['סה"כ']
        t.setHorizontalHeaderLabels(headers)
        t.setRowCount(len(pivot) + 1)
        for r, (cat, row) in enumerate(pivot.iterrows()):
            t.setItem(r, 0, QTableWidgetItem(str(cat)))
            for c, ym in enumerate(months):
                v = int(row.get(ym, 0))
                it = QTableWidgetItem(str(v) if v else "")
                it.setTextAlignment(Qt.AlignCenter)
                t.setItem(r, c + 1, it)
            total_item = QTableWidgetItem(str(int(row.sum())))
            total_item.setTextAlignment(Qt.AlignCenter)
            f = QFont(); f.setBold(True); total_item.setFont(f)
            t.setItem(r, len(months) + 1, total_item)
        # שורת סה"כ
        tr = len(pivot)
        total_lbl = QTableWidgetItem("סה\"כ")
        f = QFont(); f.setBold(True); total_lbl.setFont(f)
        t.setItem(tr, 0, total_lbl)
        col_totals = pivot.sum()
        for c, ym in enumerate(months):
            it = QTableWidgetItem(str(int(col_totals.get(ym, 0))))
            it.setTextAlignment(Qt.AlignCenter)
            f = QFont(); f.setBold(True); it.setFont(f)
            it.setBackground(QColor('#eaf4fb'))
            t.setItem(tr, c + 1, it)
        grand = QTableWidgetItem(str(int(col_totals.sum())))
        f = QFont(); f.setBold(True); grand.setFont(f)
        grand.setBackground(QColor('#d5e8f5'))
        t.setItem(tr, len(months) + 1, grand)
        t.resizeColumnsToContents()

    def _fill_snap_fc_table(self, results: dict, series: pd.Series):
        models = ['arima','prophet','xgboost']
        dfs    = {m: results[m].set_index('year_month')
                  for m in models if m in results}
        months = results['arima']['year_month'].tolist() if 'arima' in results else []
        prev   = int(series.values[-1]) if len(series) else 0
        t      = self.snap_fc_table
        t.setRowCount(len(months))
        for row, ym in enumerate(months):
            vals = [int(dfs[m].loc[ym,'forecast'])
                    if ym in dfs.get(m,{}).index else 0 for m in models]
            avg = round(sum(vals)/len(vals)) if vals else 0
            lo  = int(dfs['arima'].loc[ym,'lower']) if 'arima' in dfs and ym in dfs['arima'].index else avg
            hi  = int(dfs['arima'].loc[ym,'upper']) if 'arima' in dfs and ym in dfs['arima'].index else avg
            pct = round((avg-prev)/prev*100,1) if prev else 0
            cells = [
                (ym,           '#2c3e50', False, False),
                (str(vals[0]), '#3498db', True,  False),
                (str(vals[1]), '#27ae60', True,  False),
                (str(vals[2]), '#e67e22', True,  False),
                (str(avg),     '#2c3e50', True,  True),
                (f"{lo}–{hi}", '#7f8c8d', True,  False),
                (f"{'+' if pct>=0 else ''}{pct}%",
                 '#27ae60' if pct>=0 else '#e74c3c', True, False),
            ]
            for col,(txt,clr,ctr,bold) in enumerate(cells):
                it = QTableWidgetItem(txt)
                it.setTextAlignment(Qt.AlignCenter if ctr else Qt.AlignRight|Qt.AlignVCenter)
                it.setForeground(QColor(clr))
                if bold: it.setBackground(QColor('#eaf4fb')); f=QFont(); f.setBold(True); it.setFont(f)
                t.setItem(row, col, it)
            prev = avg
        t.resizeColumnsToContents()

    def _fill_snap_cat_table(self, by_cat: dict):
        if not by_cat:
            return
        cats   = list(by_cat.keys())
        months = by_cat[cats[0]]['avg']['year_month'].tolist()
        t      = self.snap_cat_table
        t.setColumnCount(len(months) + 2)
        t.setHorizontalHeaderLabels(["זיהוי"] + months + ['סה"כ 6M'])
        t.setRowCount(len(cats))
        for r, cat in enumerate(cats):
            avg_vals = by_cat[cat]['avg']['forecast'].values
            t.setItem(r, 0, QTableWidgetItem(str(cat)))
            for c, v in enumerate(avg_vals):
                it = QTableWidgetItem(str(int(v)))
                it.setTextAlignment(Qt.AlignCenter)
                t.setItem(r, c + 1, it)
            total = QTableWidgetItem(str(int(avg_vals.sum())))
            total.setTextAlignment(Qt.AlignCenter)
            f = QFont(); f.setBold(True); total.setFont(f)
            total.setBackground(QColor('#eafaf1'))
            t.setItem(r, len(months) + 1, total)
        t.resizeColumnsToContents()

    # ────────────────────────────────────────────
    #  הרץ תחזית (Tab 1 + Tabs 2/3/4)
    # ────────────────────────────────────────────
    def _run_forecast(self):
        codes = self._sel_codes(); cats = self._sel_cats()
        if not codes:
            QMessageBox.warning(self,"בחירה","יש לבחור לפחות סניף אחד"); return
        if not cats:
            QMessageBox.warning(self,"בחירה","יש לבחור לפחות קטגוריית מוצר אחת"); return

        hist = self.fdb.get_history(branches=codes, luggage_types=cats)
        if hist.empty:
            QMessageBox.warning(self,"נתונים","אין היסטוריה לבחירה זו"); return

        self._hist_df = hist
        self._sel_labels_last = [i.text() for i in self.branch_list.selectedItems()]
        self._sel_cats_last   = cats

        # עדכן Tabs 2, 3, 4 מיד (ללא מודלים)
        self._update_dist_tabs(hist, codes, cats)

        # הפעל מודלים (Tab 1) ברקע
        agg = hist.groupby('year_month')['quantity'].sum().sort_index()
        self._series = pd.Series(agg.values, index=agg.index)
        horizon = self._get_horizon()
        self._last_horizon = horizon

        title = self._make_title(self._sel_labels_last, cats, horizon)
        self.fc_title.setText(f"מריץ מודלים… {title}")
        self.run_btn.setEnabled(False)
        self.progress_bar.setVisible(True)

        self._worker = ForecastWorker(
            self._series, horizon, self.fdb.get_events(), self._build_context(),
            branches=codes, categories=cats, persist=True)
        self._worker.progress.connect(self.status_label.setText)
        self._worker.finished.connect(self._on_forecast_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _update_dist_tabs(self, hist: pd.DataFrame, codes, cats):
        last3 = sorted(hist['year_month'].unique())[-3:]
        hist3 = hist[hist['year_month'].isin(last3)]

        # Tab 2 — לפי קטגוריה
        cat_avg = (hist3.groupby('luggage_type')['quantity']
                   .sum() / len(last3)).sort_values(ascending=True)
        total_c = cat_avg.sum()
        self.cat_chart.plot(cat_avg, 'ממוצע יחידות/חודש', '#27ae60',
                            f"ממוצע חודשי לפי קטגוריה — {len(codes)} סניפים")
        self.cat_table.setRowCount(len(cat_avg))
        for r, (cat, v) in enumerate(cat_avg.sort_values(ascending=False).items()):
            pct = round(v / total_c * 100, 1) if total_c else 0
            for c, txt in enumerate([cat, str(round(v,1)), f"{pct}%"]):
                item = QTableWidgetItem(txt)
                item.setTextAlignment(Qt.AlignCenter if c else Qt.AlignRight|Qt.AlignVCenter)
                self.cat_table.setItem(r, c, item)

        # Tab 3 — לפי סניף
        br_avg = (hist3.groupby('branch')['quantity']
                  .sum() / len(last3)).sort_values(ascending=True)
        total_b = br_avg.sum()
        br_labels = br_avg.copy()
        br_labels.index = [get_display_label(str(b)) for b in br_avg.index]
        self.br_chart.plot(br_labels, 'ממוצע יחידות/חודש', '#3498db',
                           f"ממוצע חודשי לפי סניף — {len(cats)} קטגוריות")
        self.br_table.setRowCount(len(br_avg))
        for r, (br, v) in enumerate(br_avg.sort_values(ascending=False).items()):
            pct = round(v / total_b * 100, 1) if total_b else 0
            lbl = get_display_label(str(br))
            for c, txt in enumerate([lbl, str(round(v,1)), f"{pct}%"]):
                item = QTableWidgetItem(txt)
                item.setTextAlignment(Qt.AlignCenter if c else Qt.AlignRight|Qt.AlignVCenter)
                self.br_table.setItem(r, c, item)

        # Tab 4 — עונתיות
        self.sea_chart.plot(hist, self._make_title(
            self._sel_labels_last, cats, self._get_horizon()))

    def _validate_forecast_data(self, results: dict) -> list[str]:
        """מחזיר רשימת אזהרות על ערכים חריגים בתחזיות."""
        warnings = []
        hist_max = float(self._series.max()) if len(self._series) else 0
        hist_last = float(self._series.iloc[-1]) if len(self._series) else 0

        for model in ('arima', 'prophet', 'xgboost'):
            df = results.get(model)
            if df is None or df.empty:
                continue
            for _, row in df.iterrows():
                val = float(row.get('forecast', 0))
                ym  = row.get('year_month', '?')
                if val < 0:
                    warnings.append(f"{model.upper()} {ym}: ערך שלילי ({val:.0f})")
                if hist_last > 0 and val > hist_last * 3:
                    warnings.append(f"{model.upper()} {ym}: קפיצה של מעל 300% מהחודש הקודם ({val:.0f})")
                if hist_max > 0 and val > hist_max * 5:
                    warnings.append(f"{model.upper()} {ym}: מעל פי 5 מהמקסימום ההיסטורי ({val:.0f} > {hist_max*5:.0f})")
        return warnings

    def _on_forecast_done(self, results):
        self._results = results
        self.run_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        title = self._make_title(self._sel_labels_last,
                                  self._sel_cats_last, self._last_horizon)
        self.fc_title.setText(title)
        self.fc_chart.plot(self._series, results)
        self._fill_fc_table(results)
        self._fill_nv(results.get('newsvendor', {}))
        self._fill_desc(results.get('descriptions', {}), results.get('metrics', {}))
        run_id = results.get('run_id')
        suffix = f"  ·  run #{run_id}" if run_id else ''
        self.status_label.setText(f"הושלם ✓{suffix}")

        issues = self._validate_forecast_data(results)
        if issues:
            from logger import logger
            logger.warning("forecast validation: %d issues: %s", len(issues), issues)
            QMessageBox.warning(
                self, "אזהרת תחזית",
                "זוהו ערכים חריגים בתחזית:\n\n" + "\n".join(f"• {w}" for w in issues[:10]),
            )

    def _on_error(self, tb):
        self.run_btn.setEnabled(True); self.proc_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.status_label.setText("שגיאה")
        QMessageBox.critical(self, "שגיאה", tb[:1000])

    def _fill_fc_table(self, results):
        models = ['arima','prophet','xgboost']
        dfs    = {m: results[m].set_index('year_month')
                  for m in models if m in results}
        months = results['arima']['year_month'].tolist() if 'arima' in results else []
        prev   = int(self._series.values[-1]) if len(self._series) else 0
        self.fc_table.setRowCount(len(months))
        for row, ym in enumerate(months):
            vals = [int(dfs[m].loc[ym,'forecast'])
                    if ym in dfs.get(m,{}).index else 0 for m in models]
            avg   = round(sum(vals)/len(vals)) if vals else 0
            lo    = int(dfs['arima'].loc[ym,'lower']) if 'arima' in dfs and ym in dfs['arima'].index else avg
            hi    = int(dfs['arima'].loc[ym,'upper']) if 'arima' in dfs and ym in dfs['arima'].index else avg
            pct   = round((avg-prev)/prev*100,1) if prev else 0
            cells = [
                (ym,             '#2c3e50', False, False),
                (str(vals[0]),   '#3498db', True,  False),
                (str(vals[1]),   '#27ae60', True,  False),
                (str(vals[2]),   '#e67e22', True,  False),
                (str(avg),       '#2c3e50', True,  True),
                (f"{lo}–{hi}",   '#7f8c8d', True,  False),
                (f"{'+' if pct>=0 else ''}{pct}%",
                 '#27ae60' if pct>=0 else '#e74c3c', True, False),
            ]
            for col,(txt,clr,ctr,bold) in enumerate(cells):
                it = QTableWidgetItem(txt)
                it.setTextAlignment(Qt.AlignCenter if ctr else
                                    Qt.AlignRight|Qt.AlignVCenter)
                it.setForeground(QColor(clr))
                if bold: it.setBackground(QColor('#eaf4fb'))
                if bold:
                    f=QFont(); f.setBold(True); it.setFont(f)
                self.fc_table.setItem(row, col, it)
            prev = avg
        self.fc_table.resizeColumnsToContents()

    def _fill_nv(self, nv):
        for k, lbl in self.nv_vals.items():
            lbl.setText(str(nv.get(k,'—')))

    def _fill_desc(self, descs, metrics: dict | None = None):
        """מציג תיאור לכל מודל, ואם יש metrics מ-backtest, שורת אמינות צמודה."""
        lmap = {'arima':'ARIMA','prophet':'Prophet','xgboost':'XGBoost','newsvendor':'Newsvendor'}
        metrics = metrics or {}
        parts = []
        for m, d in descs.items():
            parts.append(
                f'<b style="color:#2c3e50;">{lmap.get(m,m)}:</b> '
                f'<span style="color:#555;">{d}</span>'
            )
            mk = metrics.get(m)
            if mk and mk.get('mae') is not None:
                mae = mk['mae']
                mape = mk.get('mape')
                accuracy = (
                    f' &nbsp;·&nbsp; <span style="color:#888;">'
                    f'אמינות (test {mk["test_n"]}חודשים): MAE ±{mae:.0f}'
                    + (f', MAPE {mape:.1f}%' if mape is not None else '')
                    + '</span>'
                )
                parts.append(accuracy)
            parts.append('<br>')
        self.fc_desc.setHtml(''.join(parts))

    # ────────────────────────────────────────────
    #  תכנון רכש (Tab 5)
    # ────────────────────────────────────────────
    def _run_procurement(self):
        codes = self._sel_codes(); cats = self._sel_cats()
        if not codes:
            QMessageBox.warning(self,"בחירה","יש לבחור לפחות סניף אחד"); return
        if not cats:
            QMessageBox.warning(self,"בחירה","יש לבחור לפחות קטגוריית מוצר אחת"); return

        hist = self.fdb.get_history(branches=codes, luggage_types=cats)
        if hist.empty:
            QMessageBox.warning(self,"נתונים","אין היסטוריה לבחירה זו"); return

        self.proc_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.status_label.setText("מחשב רכש…")
        self.tabs.setCurrentIndex(4)

        self._pw = ProcurementWorker(hist, self.fdb.get_events(), self._build_context())
        self._pw.progress.connect(self.status_label.setText)
        self._pw.finished.connect(self._on_procurement_done)
        self._pw.error.connect(self._on_error)
        self._pw.start()

    def _on_procurement_done(self, results: dict):
        self._proc_results = results
        self.proc_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.status_label.setText("תכנון רכש הושלם ✓")
        self._refresh_procurement_table()

    def _refresh_procurement_table(self):
        if not self._proc_results:
            return
        horizon_map = {"חודש הבא":1, "3 חודשים":3, "6 חודשים":6}
        h = horizon_map.get(self.proc_horizon.currentText(), 1)

        cats = sorted(self._proc_results.keys())
        self.proc_table.setRowCount(len(cats))

        for row, cat in enumerate(cats):
            d  = self._proc_results[cat]
            ar = d['arima']['forecast'].values[:h].sum()
            xg = d['xgboost']['forecast'].values[:h].sum()
            av = round((ar + xg) / 2)
            nv = d['newsvendor']

            cells = [
                (cat,                  '#2c3e50', False),
                (str(d['hist_avg3']),  '#7f8c8d', True),
                (str(int(ar)),         '#3498db', True),
                (str(int(xg)),         '#e67e22', True),
                (str(av),              '#2c3e50', True),
                (str(nv['safety_stock']),    '#e67e22', True),
                (str(int(nv['order_quantity'])), '#27ae60', True),
            ]
            for col, (txt, clr, ctr) in enumerate(cells):
                it = QTableWidgetItem(txt)
                it.setTextAlignment(Qt.AlignCenter if ctr else
                                    Qt.AlignRight|Qt.AlignVCenter)
                it.setForeground(QColor(clr))
                if col == 6:
                    it.setBackground(QColor('#eafaf1'))
                    f = QFont(); f.setBold(True); it.setFont(f)
                self.proc_table.setItem(row, col, it)

        self.proc_table.resizeColumnsToContents()
