# -*- coding: utf-8 -*-
"""
tabs/min_stock_tab.py — מסך "מלאי מינימום מומלץ".

מציג שורה לכל (סניף × קטגוריית-זיהוי) בעלת ערך כלשהו:
    סניף | קטגוריה | rate_1m | rate_3m | rate_12m | מלאי נוכחי | מומלץ | פער

slider של ימי-אספקה (2–21, ברירת-מחדל 7) קובע את המכפיל ב-recommended_min.
"""
from __future__ import annotations
from datetime import datetime
import pandas as pd
from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QPushButton, QSlider, QTableWidget, QTableWidgetItem,
    QHeaderView, QFileDialog, QMessageBox, QTextEdit,
)
from qtpy.QtGui import QColor, QBrush

from tabs._base import BaseTabWorker, format_error_for_user
from logger import logger
from domain_repository import get_branch_name


class MinStockWorker(BaseTabWorker):
    def __init__(self, lead_time_days: int, parent=None):
        super().__init__(parent)
        self.lead_time_days = lead_time_days

    def _do(self):
        from min_stock_calculator import compute_min_stock
        self.emit_progress(f"מחשב המלצה ל-{self.lead_time_days} ימי אספקה…")
        df = compute_min_stock(lead_time_days=self.lead_time_days)
        self.emit_progress(f"חישוב הסתיים: {len(df)} שורות")
        return df


class MinStockTab(QWidget):
    def __init__(self):
        super().__init__()
        self._df: pd.DataFrame | None = None
        self._worker: MinStockWorker | None = None
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(12)

        title = QLabel("מלאי מינימום מומלץ")
        title.setStyleSheet("font-size: 18px; font-weight: bold; padding: 4px;")
        layout.addWidget(title)

        explain = QLabel(
            "המלצה אוטומטית למלאי-מינימום פר (סניף × קטגוריית-מזוודה), "
            "מבוסס על יציאות-ב-30/90/365 הימים האחרונים. הסניפים שמוצגים: "
            "סניפים בעלי ≥3 תנועות-לקוח-מבוטח ב-12 חודשים האחרונים, "
            "או ≥1 בחודש האחרון. הקטגוריה היא תוצר של זיהוי-המוצר."
        )
        explain.setWordWrap(True)
        explain.setStyleSheet("color: #555; padding: 4px;")
        layout.addWidget(explain)

        # Slider של ימי-אספקה
        slider_group = QGroupBox('זמן אספקה מהמרלו"ג')
        slider_layout = QHBoxLayout()

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setMinimum(2)
        self.slider.setMaximum(21)
        self.slider.setValue(7)
        self.slider.setTickPosition(QSlider.TicksBelow)
        self.slider.setTickInterval(1)
        self.slider.valueChanged.connect(self._on_slider_changed)

        self.slider_label = QLabel("7 ימים")
        self.slider_label.setStyleSheet("font-weight: bold; min-width: 80px;")

        slider_layout.addWidget(QLabel("2 ימים"))
        slider_layout.addWidget(self.slider, 1)
        slider_layout.addWidget(QLabel("21 ימים"))
        slider_layout.addSpacing(20)
        slider_layout.addWidget(self.slider_label)
        slider_group.setLayout(slider_layout)
        layout.addWidget(slider_group)

        # Buttons
        btn_row = QHBoxLayout()
        self.compute_btn = QPushButton("חשב המלצה")
        self.compute_btn.setStyleSheet(
            "QPushButton { background:#0a7; color:white; padding:6px 16px; "
            "font-weight:bold; border-radius:4px; } "
            "QPushButton:disabled { background:#aaa; }"
        )
        self.compute_btn.clicked.connect(self._on_compute)

        self.export_btn = QPushButton("ייצוא לאקסל")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._on_export)

        btn_row.addWidget(self.compute_btn)
        btn_row.addWidget(self.export_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Status
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #555; padding: 2px;")
        layout.addWidget(self.status_label)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(9)
        self.table.setHorizontalHeaderLabels([
            "סניף", "שם סניף", "קטגוריה",
            "קצב לחודש", "קצב לשלושה חודשים", "קצב ל-12 חודשים",
            "מלאי נוכחי", "מומלץ מינימום", "פער",
        ])
        # תצוגה אסתטית יותר — שורות גבוהות, header גדול, רווחים נוחים
        self.table.setStyleSheet("""
            QTableWidget {
                gridline-color: #d0d0d0;
                font-size: 12px;
            }
            QHeaderView::section {
                background-color: #34495e;
                color: white;
                padding: 8px 10px;
                font-weight: bold;
                font-size: 12px;
                border: none;
                border-right: 1px solid #2c3e50;
            }
            QTableWidget::item {
                padding: 6px 10px;
            }
        """)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table, 1)

        self.setLayout(layout)

    # ---- Slots ----
    def _on_slider_changed(self, val: int):
        self.slider_label.setText(f"{val} ימים")

    def _on_compute(self):
        if self._worker is not None and self._worker.isRunning():
            return
        self.compute_btn.setEnabled(False)
        self.export_btn.setEnabled(False)
        self.status_label.setText("מתחיל חישוב…")
        self.table.setRowCount(0)

        days = self.slider.value()
        self._worker = MinStockWorker(lead_time_days=days, parent=self)
        self._worker.progress.connect(self.status_label.setText)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_done(self, df: pd.DataFrame):
        self.compute_btn.setEnabled(True)
        if df is None or df.empty:
            self.status_label.setText("אין נתונים — בדוק שיש תנועות-לקוח-מבוטח ב-12 חודשים האחרונים.")
            return
        self._df = df
        self._fill_table(df)
        self.export_btn.setEnabled(True)
        self.status_label.setText(f"מציג {len(df)} שורות ({df['branch'].nunique()} סניפים).")

    def _on_error(self, tb: str):
        self.compute_btn.setEnabled(True)
        self.status_label.setText("שגיאה בחישוב.")
        QMessageBox.critical(self, "שגיאה", format_error_for_user(tb))

    def _fill_table(self, df: pd.DataFrame):
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(df))

        # rate הוא יחידות-ליום. ערך תחת 1 — מציג "<1" כי הקצב פחות
        # ממזוודה אחת ליום (לעיתים קרובות שברים נמוכים מאוד).
        def _fmt_rate(r: float) -> str:
            return "<1" if r < 1 else f"{r:.2f}"

        for i, (_, row) in enumerate(df.iterrows()):
            branch = str(row['branch'])
            branch_name = get_branch_name(branch) or ''

            cells = [
                branch,
                branch_name,
                row['category'] or '',
                _fmt_rate(row['rate_1m']),
                _fmt_rate(row['rate_3m']),
                _fmt_rate(row['rate_12m']),
                f"{row['current_stock']:.0f}",
                str(row['recommended_min']),
                f"{row['gap']:+.0f}",
            ]
            for j, val in enumerate(cells):
                item = QTableWidgetItem(val)
                if j >= 3:
                    item.setTextAlignment(Qt.AlignCenter)
                if j == 8:
                    gap = float(row['gap'])
                    if gap < 0:
                        item.setForeground(QBrush(QColor(200, 0, 0)))
                    elif gap > 5:
                        item.setForeground(QBrush(QColor(0, 130, 0)))
                self.table.setItem(i, j, item)

        self.table.resizeColumnsToContents()
        # רוחב מינימלי לעמודות "קצב" (3 כותרות-עברית ארוכות)
        for col in [3, 4, 5]:
            current = self.table.columnWidth(col)
            self.table.setColumnWidth(col, max(current, 140))
        # קטגוריה רחבה במיוחד
        self.table.setColumnWidth(2, max(self.table.columnWidth(2), 200))
        self.table.setSortingEnabled(True)

    def _on_export(self):
        if self._df is None or self._df.empty:
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        days = self.slider.value()
        default_name = f"min_stock_{days}d_{ts}.xlsx"
        path, _ = QFileDialog.getSaveFileName(
            self, "שמירה כאקסל", default_name, "Excel (*.xlsx)"
        )
        if not path:
            return

        df = self._df.copy()
        # תרגום שמות-עמודות לעברית + שם-סניף
        df['שם סניף'] = df['branch'].map(lambda b: get_branch_name(str(b)) or '')
        df = df[['branch', 'שם סניף', 'category', 'rate_1m', 'rate_3m',
                 'rate_12m', 'rate_used', 'current_stock', 'recommended_min', 'gap']]
        df.columns = ['סניף', 'שם סניף', 'קטגוריה', 'קצב 1ח', 'קצב 3ח',
                      'קצב 12ח', 'קצב בשימוש', 'מלאי נוכחי', 'מומלץ מינימום', 'פער']
        try:
            df.to_excel(path, index=False)
            QMessageBox.information(self, "נשמר", f"נשמר בקובץ:\n{path}")
        except Exception as e:
            logger.exception("Export failed")
            QMessageBox.critical(self, "שגיאה", f"שמירה נכשלה: {e}")
