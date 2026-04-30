# -*- coding: utf-8 -*-
import calendar
from datetime import datetime
import pandas as pd
from qtpy.QtWidgets import (
    QWidget, QVBoxLayout, QGridLayout, QGroupBox, QLabel,
    QPushButton, QComboBox, QTextEdit, QMessageBox,
)
from qtpy.QtCore import Qt, QThread, Signal as pyqtSignal

from fetch_combined import fetch_with_cache, combine_data
from inventory_analysis import filter_by_attributes


class InventoryAnalysisWorker(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(object)   # dict: {sheet_name: pd.DataFrame}
    error    = pyqtSignal(str)

    def __init__(self, start_date, end_date, brand_filter, material_filter, size_filter):
        super().__init__()
        self.start_date      = start_date
        self.end_date        = end_date
        self.brand_filter    = brand_filter
        self.material_filter = material_filter
        self.size_filter     = size_filter

    def run(self):
        try:
            self.progress.emit(f"מושך נתונים {self.start_date} עד {self.end_date}…")
            documents, logfile = fetch_with_cache(self.start_date, self.end_date)
            self.progress.emit(f"✓ {len(documents)} מסמכים, {len(logfile)} תנועות")
            combined = combine_data(documents, logfile)
            combined = combined[combined['סטטוס'] == 'סופית']
            combined = combined[combined['זיהוי מזוודה'].notna()]
            self.progress.emit("מסנן לפי מאפיינים…")
            sheets = filter_by_attributes(combined, self.brand_filter,
                                          self.material_filter, self.size_filter)
            self.finished.emit(sheets)
        except Exception as e:
            import traceback; self.error.emit(traceback.format_exc())


class InventoryAnalysisTab(QWidget):
    def __init__(self):
        super().__init__()
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(20)

        title = QLabel("תחקור התנהלות מלאי")
        title.setStyleSheet("font-size: 24px; font-weight: bold; color: #2c3e50;")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        date_group = QGroupBox("בחירת תקופה")
        date_layout = QGridLayout()
        current_year = datetime.now().year

        date_layout.addWidget(QLabel("מתאריך:"), 0, 0)
        self.from_month = QComboBox()
        self.from_month.addItems([f"{i:02d}" for i in range(1, 13)])
        date_layout.addWidget(self.from_month, 0, 1)
        self.from_year = QComboBox()
        self.from_year.addItems([str(y) for y in range(current_year - 2, current_year + 2)])
        self.from_year.setCurrentText(str(current_year))
        date_layout.addWidget(self.from_year, 0, 2)

        date_layout.addWidget(QLabel("עד תאריך:"), 1, 0)
        self.to_month = QComboBox()
        self.to_month.addItems([f"{i:02d}" for i in range(1, 13)])
        self.to_month.setCurrentText(f"{datetime.now().month:02d}")
        date_layout.addWidget(self.to_month, 1, 1)
        self.to_year = QComboBox()
        self.to_year.addItems([str(y) for y in range(current_year - 2, current_year + 2)])
        self.to_year.setCurrentText(str(current_year))
        date_layout.addWidget(self.to_year, 1, 2)

        date_group.setLayout(date_layout)
        layout.addWidget(date_group)

        filters_group = QGroupBox("סינון לפי מאפיינים")
        filters_layout = QGridLayout()

        filters_layout.addWidget(QLabel("דרגת מותג:"), 0, 0)
        self.brand_combo = QComboBox()
        self.brand_combo.addItems(["הכל", "קלאסית", "מותג", "מותג על"])
        filters_layout.addWidget(self.brand_combo, 0, 1)

        filters_layout.addWidget(QLabel("חומר:"), 1, 0)
        self.material_combo = QComboBox()
        self.material_combo.addItems(["הכל", "קשיחה", "רכה"])
        filters_layout.addWidget(self.material_combo, 1, 1)

        filters_layout.addWidget(QLabel("גודל:"), 2, 0)
        self.size_combo = QComboBox()
        self.size_combo.addItems(["הכל", "טרולי", "בינונית", "גדולה", "ענקית"])
        filters_layout.addWidget(self.size_combo, 2, 1)

        filters_group.setLayout(filters_layout)
        layout.addWidget(filters_group)

        self.run_btn = QPushButton("יצירת דוח תחקור מלאי")
        self.run_btn.setStyleSheet("""
            QPushButton { background-color:#16a085; color:white; font-size:18px;
                          font-weight:bold; padding:15px; border-radius:8px; }
            QPushButton:hover { background-color:#138d75; }
        """)
        self.run_btn.clicked.connect(self._generate_analysis)
        layout.addWidget(self.run_btn)

        self.status_text = QTextEdit()
        self.status_text.setReadOnly(True)
        self.status_text.setStyleSheet("background-color:#ecf0f1; font-family:Consolas;")
        layout.addWidget(self.status_text)

        layout.addStretch()
        self.setLayout(layout)

    def _generate_analysis(self):
        self.status_text.clear()
        self.run_btn.setEnabled(False)

        from_month = int(self.from_month.currentText())
        from_year  = int(self.from_year.currentText())
        to_month   = int(self.to_month.currentText())
        to_year    = int(self.to_year.currentText())
        last_day   = calendar.monthrange(to_year, to_month)[1]
        start_date = f"{from_year}-{from_month:02d}-01"
        end_date   = f"{to_year}-{to_month:02d}-{last_day}"

        brand_filter    = None if self.brand_combo.currentText()    == "הכל" else self.brand_combo.currentText()
        material_filter = None if self.material_combo.currentText() == "הכל" else self.material_combo.currentText()
        size_filter     = None if self.size_combo.currentText()     == "הכל" else self.size_combo.currentText()

        self._worker = InventoryAnalysisWorker(
            start_date, end_date, brand_filter, material_filter, size_filter)
        self._worker.progress.connect(self.status_text.append)
        self._worker.finished.connect(
            lambda sheets: self._on_analysis_done(sheets, from_year, from_month, to_year, to_month))
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_analysis_done(self, sheets, from_year, from_month, to_year, to_month):
        self.run_btn.setEnabled(True)
        if not sheets:
            self.status_text.append("\n✗ לא נמצאו נתונים עבור המסננים שנבחרו")
            QMessageBox.information(self, "מידע", "לא נמצאו נתונים עבור המסננים שנבחרו")
            return
        try:
            filename  = f"inventory_analysis_{from_year}{from_month:02d}_{to_year}{to_month:02d}.xlsx"
            all_data  = pd.concat(sheets.values(), ignore_index=True)
            summary   = pd.DataFrame([
                {'קטגוריה': name, 'כמות שורות': len(data)}
                for name, data in sheets.items()
            ])
            summary.loc[len(summary)] = {'קטגוריה': 'סה"כ', 'כמות שורות': len(all_data)}

            with pd.ExcelWriter(filename, engine='openpyxl') as writer:
                summary.to_excel(writer, sheet_name='סיכום', index=False)
                all_data.to_excel(writer, sheet_name='כל הנתונים', index=False)
                for sheet_name, data in sheets.items():
                    data.to_excel(writer, sheet_name=sheet_name, index=False)
                    self.status_text.append(f"  ✓ {sheet_name}: {len(data)} שורות")

            self.status_text.append(f"\n✓ הקובץ נוצר בהצלחה: {filename}")
            QMessageBox.information(self, "הצלחה", f"הדוח נוצר בהצלחה!\n{filename}")
        except Exception as e:
            self.status_text.append(f"\n✗ שגיאה בשמירה: {e}")
            QMessageBox.critical(self, "שגיאה", str(e))

    def _on_error(self, tb):
        self.run_btn.setEnabled(True)
        self.status_text.append(f"\n✗ שגיאה: {tb[:800]}")
        QMessageBox.critical(self, "שגיאה", tb[:500])
