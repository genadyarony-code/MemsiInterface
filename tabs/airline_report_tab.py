# -*- coding: utf-8 -*-
import calendar
from datetime import date
import pandas as pd
from qtpy.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QPushButton, QComboBox, QListWidget, QTextEdit, QMessageBox,
)
from qtpy.QtCore import Qt, QThread, Signal as pyqtSignal
from dateutil.relativedelta import relativedelta

from fetch_combined import fetch_with_cache, combine_data, TARGET_CUSTOMERS


class MultiMonthReportWorker(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(object)   # dict: {sheet_name: pd.DataFrame}
    error    = pyqtSignal(str)

    def __init__(self, customer_ids, from_date, to_date):
        super().__init__()
        self.customer_ids = customer_ids
        self.from_date    = from_date
        self.to_date      = to_date

    def run(self):
        try:
            sheets = {}
            current = self.from_date
            while current <= self.to_date:
                last_day    = calendar.monthrange(current.year, current.month)[1]
                month_start = current.strftime("%Y-%m-01")
                month_end   = current.strftime(f"%Y-%m-{last_day}")
                ym          = current.strftime("%Y-%m")
                self.progress.emit(f"מעבד {ym}…")
                documents, logfile = fetch_with_cache(month_start, month_end)
                combined = combine_data(documents, logfile)
                combined = combined[combined['סטטוס'] == 'סופית']
                combined = combined[combined['מספר לקוח'].isin(self.customer_ids)]
                if not combined.empty:
                    sheets[ym] = combined
                current = (current + relativedelta(months=1)).replace(day=1)
            self.finished.emit(sheets)
        except Exception as e:
            import traceback; self.error.emit(traceback.format_exc())


class AirlineReportTab(QWidget):
    def __init__(self):
        super().__init__()
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(20)

        title = QLabel("דוח לפי לקוחות נבחרים")
        title.setStyleSheet("font-size: 24px; font-weight: bold; color: #2c3e50;")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        customers_group = QGroupBox("בחירת לקוחות (ניתן לבחור מספר לקוחות)")
        customers_layout = QVBoxLayout()
        self.customers_list = QListWidget()
        self.customers_list.setSelectionMode(QListWidget.MultiSelection)
        for customer_id in TARGET_CUSTOMERS:
            self.customers_list.addItem(customer_id)
        customers_layout.addWidget(self.customers_list)
        customers_group.setLayout(customers_layout)
        layout.addWidget(customers_group)

        period_group = QGroupBox("בחירת תקופה")
        period_layout = QHBoxLayout()
        current_year = date.today().year

        period_layout.addWidget(QLabel("מחודש:"))
        self.from_month = QComboBox()
        self.from_month.addItems([f"{i:02d}" for i in range(1, 13)])
        period_layout.addWidget(self.from_month)

        self.from_year = QComboBox()
        self.from_year.addItems([str(y) for y in range(current_year - 2, current_year + 2)])
        self.from_year.setCurrentText(str(current_year))
        period_layout.addWidget(self.from_year)

        period_layout.addWidget(QLabel("עד חודש:"))
        self.to_month = QComboBox()
        self.to_month.addItems([f"{i:02d}" for i in range(1, 13)])
        period_layout.addWidget(self.to_month)

        self.to_year = QComboBox()
        self.to_year.addItems([str(y) for y in range(current_year - 2, current_year + 2)])
        self.to_year.setCurrentText(str(current_year))
        period_layout.addWidget(self.to_year)

        period_group.setLayout(period_layout)
        layout.addWidget(period_group)

        self.run_btn = QPushButton("יצירת דוח לפי לקוחות נבחרים")
        self.run_btn.setStyleSheet("""
            QPushButton { background-color:#9b59b6; color:white; font-size:18px;
                          font-weight:bold; padding:15px; border-radius:8px; }
            QPushButton:hover { background-color:#8e44ad; }
        """)
        self.run_btn.clicked.connect(self.generate_airline_report)
        layout.addWidget(self.run_btn)

        self.status_text = QTextEdit()
        self.status_text.setReadOnly(True)
        self.status_text.setStyleSheet("background-color:#ecf0f1; font-family:Consolas;")
        layout.addWidget(self.status_text)

        layout.addStretch()
        self.setLayout(layout)

    def generate_airline_report(self):
        self.status_text.clear()
        selected_items = self.customers_list.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "אזהרה", "יש לבחור לפחות לקוח אחד"); return

        customer_ids = [item.text() for item in selected_items]
        from_date = date(int(self.from_year.currentText()), int(self.from_month.currentText()), 1)
        to_date   = date(int(self.to_year.currentText()),   int(self.to_month.currentText()),   1)

        self.run_btn.setEnabled(False)
        self.status_text.append(f"יוצר דוח עבור {len(customer_ids)} לקוחות…\n")

        self._worker = MultiMonthReportWorker(customer_ids, from_date, to_date)
        self._worker.progress.connect(self.status_text.append)
        self._worker.finished.connect(
            lambda sheets: self._on_airline_done(sheets, customer_ids, from_date, to_date))
        self._worker.error.connect(self._on_airline_error)
        self._worker.start()

    def _on_airline_done(self, sheets, customer_ids, from_date, to_date):
        try:
            customers_str = '_'.join(customer_ids[:3]) if len(customer_ids) <= 3 else f"{len(customer_ids)}_customers"
            filename = (f"customers_report_{customers_str}_"
                        f"{from_date.year}{from_date.month:02d}_"
                        f"{to_date.year}{to_date.month:02d}.xlsx")
            with pd.ExcelWriter(filename, engine='openpyxl') as writer:
                for ym, df in sheets.items():
                    df.to_excel(writer, sheet_name=ym, index=False)
                    self.status_text.append(f"  ✓ {ym}: {len(df)} שורות")
            self.status_text.append(f"\n✓ הקובץ נוצר בהצלחה: {filename}")
            QMessageBox.information(self, "הצלחה", f"הדוח נוצר בהצלחה!\n{filename}")
        except Exception as e:
            self.status_text.append(f"\n✗ שגיאה בשמירה: {e}")
            QMessageBox.critical(self, "שגיאה", str(e))
        finally:
            self.run_btn.setEnabled(True)

    def _on_airline_error(self, tb):
        self.status_text.append(f"\n✗ שגיאה:\n{tb[:800]}")
        QMessageBox.critical(self, "שגיאה", tb[:500])
        self.run_btn.setEnabled(True)
