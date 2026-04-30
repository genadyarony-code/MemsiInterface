# -*- coding: utf-8 -*-
import calendar
from datetime import datetime
import pandas as pd
from qtpy.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QPushButton, QComboBox, QTextEdit, QMessageBox,
)
from qtpy.QtCore import Qt, QThread, Signal as pyqtSignal

from fetch_combined import fetch_with_cache, combine_data, TARGET_CUSTOMERS
from pricing_data import get_supplier_payment


class ReportWorker(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(object)   # pd.DataFrame
    error    = pyqtSignal(str)

    def __init__(self, start_date, end_date, force_refresh=False, year_month=None):
        super().__init__()
        self.start_date    = start_date
        self.end_date      = end_date
        self.force_refresh = force_refresh
        self.year_month    = year_month

    def run(self):
        try:
            if self.force_refresh and self.year_month:
                from cache_manager import CacheManager
                self.progress.emit(f"מוחק נתוני {self.year_month} מהמטמון…")
                cm = CacheManager(); cm.clear_month_data(self.year_month); cm.close()
            self.progress.emit("מושך נתונים מ-Priority…")
            documents, logfile = fetch_with_cache(self.start_date, self.end_date)
            self.progress.emit(f"עיבוד {len(documents)} מסמכים…")
            combined = combine_data(documents, logfile)
            self.finished.emit(combined)
        except Exception as e:
            import traceback; self.error.emit(traceback.format_exc())


class ReportGeneratorTab(QWidget):
    def __init__(self):
        super().__init__()
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(20)

        title = QLabel("יצירת דוח חיובים ותשלומים")
        title.setStyleSheet("font-size: 24px; font-weight: bold; color: #2c3e50;")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        date_group = QGroupBox("בחירת תקופה")
        date_group.setStyleSheet("QGroupBox { font-size: 16px; font-weight: bold; }")
        date_layout = QHBoxLayout()

        date_layout.addWidget(QLabel("חודש:"))
        self.month_combo = QComboBox()
        self.month_combo.addItems([f"{i:02d}" for i in range(1, 13)])
        self.month_combo.setCurrentText(f"{datetime.now().month:02d}")
        date_layout.addWidget(self.month_combo)

        date_layout.addWidget(QLabel("שנה:"))
        self.year_combo = QComboBox()
        current_year = datetime.now().year
        self.year_combo.addItems([str(y) for y in range(current_year - 2, current_year + 2)])
        self.year_combo.setCurrentText(str(current_year))
        date_layout.addWidget(self.year_combo)

        date_group.setLayout(date_layout)
        layout.addWidget(date_group)

        buttons_layout = QHBoxLayout()

        self.run_btn = QPushButton("בצע יצירת דוח")
        self.run_btn.setStyleSheet("""
            QPushButton { background-color:#3498db; color:white; font-size:18px;
                          font-weight:bold; padding:15px; border-radius:8px; }
            QPushButton:hover { background-color:#2980b9; }
        """)
        self.run_btn.clicked.connect(self.generate_report)
        buttons_layout.addWidget(self.run_btn)

        self.refresh_btn = QPushButton("בצע יצירת דוח עם ריענון")
        self.refresh_btn.setStyleSheet("""
            QPushButton { background-color:#e67e22; color:white; font-size:18px;
                          font-weight:bold; padding:15px; border-radius:8px; }
            QPushButton:hover { background-color:#d35400; }
        """)
        self.refresh_btn.clicked.connect(self.generate_report_with_refresh)
        buttons_layout.addWidget(self.refresh_btn)

        layout.addLayout(buttons_layout)

        self.status_text = QTextEdit()
        self.status_text.setReadOnly(True)
        self.status_text.setStyleSheet("background-color:#ecf0f1; font-family:Consolas;")
        layout.addWidget(self.status_text)

        layout.addStretch()
        self.setLayout(layout)

    def _start_worker(self, force_refresh=False):
        month = int(self.month_combo.currentText())
        year  = int(self.year_combo.currentText())
        last_day   = calendar.monthrange(year, month)[1]
        start_date = f"{year}-{month:02d}-01"
        end_date   = f"{year}-{month:02d}-{last_day}"
        year_month = f"{year}-{month:02d}"

        self.status_text.clear()
        self.run_btn.setEnabled(False)
        self.refresh_btn.setEnabled(False)
        self.status_text.append(f"טוען נתונים עבור {year_month}…\n")

        self._worker = ReportWorker(start_date, end_date,
                                    force_refresh=force_refresh,
                                    year_month=year_month if force_refresh else None)
        self._worker.progress.connect(self.status_text.append)
        self._worker.finished.connect(self._on_report_ready)
        self._worker.error.connect(self._on_report_error)
        self._worker.start()

    def generate_report(self):
        self._start_worker(force_refresh=False)

    def generate_report_with_refresh(self):
        month = int(self.month_combo.currentText())
        year  = int(self.year_combo.currentText())
        year_month = f"{year}-{month:02d}"
        reply = QMessageBox.question(
            self, "אישור ריענון",
            f"פעולה זו תמחק את נתוני {year_month} מהמטמון ותמשוך נתונים חדשים מ-Priority.\nהאם להמשיך?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self._start_worker(force_refresh=True)

    def _on_report_ready(self, combined):
        try:
            with pd.ExcelWriter('combined_output.xlsx', engine='openpyxl') as writer:
                for customer_id in TARGET_CUSTOMERS:
                    customer_data = combined[combined['מספר לקוח'] == customer_id]
                    if not customer_data.empty:
                        customer_data.to_excel(writer, sheet_name=customer_id, index=False)
                suppliers_data = combined[combined['פרטים'].notna() & (combined['פרטים'] != '')].copy()
                if not suppliers_data.empty:
                    suppliers_data['תשלום לספק'] = suppliers_data.apply(
                        lambda row: get_supplier_payment(row['מקט'], row['זיהוי מזוודה'], row['כמות']), axis=1
                    )
                    suppliers_data.to_excel(writer, sheet_name='תשלום לספקים', index=False)
                combined.to_excel(writer, sheet_name='סיכום חודשי', index=False)
            self.status_text.append(f"\n✓ הקובץ נוצר בהצלחה: combined_output.xlsx")
            self.status_text.append(f"✓ סך הכל {len(combined)} שורות")
            QMessageBox.information(self, "הצלחה", "הדוח נוצר בהצלחה!")
        except Exception as e:
            self.status_text.append(f"\n✗ שגיאה בשמירה: {e}")
            QMessageBox.critical(self, "שגיאה", str(e))
        finally:
            self.run_btn.setEnabled(True)
            self.refresh_btn.setEnabled(True)

    def _on_report_error(self, tb):
        self.status_text.append(f"\n✗ שגיאה:\n{tb[:800]}")
        QMessageBox.critical(self, "שגיאה", tb[:500])
        self.run_btn.setEnabled(True)
        self.refresh_btn.setEnabled(True)
