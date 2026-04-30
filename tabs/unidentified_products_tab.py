# -*- coding: utf-8 -*-
import calendar
from datetime import datetime
import pandas as pd
from qtpy.QtWidgets import (
    QWidget, QVBoxLayout, QGridLayout, QGroupBox, QLabel,
    QPushButton, QComboBox, QTextEdit, QMessageBox, QFileDialog,
)
from qtpy.QtCore import Qt, QThread, Signal as pyqtSignal

from fetch_combined import fetch_with_cache, combine_data


class UnidentifiedExportWorker(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(object)   # pd.DataFrame | None (None = all identified)
    error    = pyqtSignal(str)

    def __init__(self, start_date, end_date):
        super().__init__()
        self.start_date = start_date
        self.end_date   = end_date

    def run(self):
        try:
            self.progress.emit(f"מושך נתונים: {self.start_date} עד {self.end_date}…")
            documents, logfile = fetch_with_cache(self.start_date, self.end_date)
            self.progress.emit(f"נטענו {len(documents)} מסמכים, {len(logfile)} תנועות")
            combined = combine_data(documents, logfile)
            combined = combined[combined['סטטוס'] == 'סופית']
            unidentified = combined[combined['זיהוי מזוודה'].isna()]
            if unidentified.empty:
                self.finished.emit(None)
            else:
                unique_products = unidentified[['תיאור מוצר']].drop_duplicates().copy()
                unique_products['דרגת מותג'] = ''
                unique_products['גודל'] = ''
                unique_products['חומר'] = ''
                self.finished.emit(unique_products)
        except Exception as e:
            import traceback; self.error.emit(traceback.format_exc())


class UnidentifiedProductsTab(QWidget):
    def __init__(self):
        super().__init__()
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(20)

        title = QLabel("זיהוי מוצרים ללא קטגוריה")
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

        self.export_btn = QPushButton("ייצא מוצרים ללא זיהוי ל-CSV")
        self.export_btn.setStyleSheet("""
            QPushButton { background-color:#e74c3c; color:white; font-size:18px;
                          font-weight:bold; padding:15px; border-radius:8px; }
            QPushButton:hover { background-color:#c0392b; }
        """)
        self.export_btn.clicked.connect(self._export_unidentified)
        layout.addWidget(self.export_btn)

        self.import_btn = QPushButton("ייבוא CSV מעודכן ועדכון מערכת")
        self.import_btn.setStyleSheet("""
            QPushButton { background-color:#27ae60; color:white; font-size:18px;
                          font-weight:bold; padding:15px; border-radius:8px; }
            QPushButton:hover { background-color:#229954; }
        """)
        self.import_btn.clicked.connect(self._import_and_update)
        layout.addWidget(self.import_btn)

        self.status_text = QTextEdit()
        self.status_text.setReadOnly(True)
        self.status_text.setStyleSheet("background-color:#ecf0f1; font-family:Consolas;")
        layout.addWidget(self.status_text)

        layout.addStretch()
        self.setLayout(layout)

    def _export_unidentified(self):
        self.status_text.clear()
        self.export_btn.setEnabled(False)

        from_month = int(self.from_month.currentText())
        from_year  = int(self.from_year.currentText())
        to_month   = int(self.to_month.currentText())
        to_year    = int(self.to_year.currentText())
        last_day   = calendar.monthrange(to_year, to_month)[1]
        start_date = f"{from_year}-{from_month:02d}-01"
        end_date   = f"{to_year}-{to_month:02d}-{last_day}"
        self._export_meta = (from_year, from_month, to_year, to_month)

        self._export_worker = UnidentifiedExportWorker(start_date, end_date)
        self._export_worker.progress.connect(self.status_text.append)
        self._export_worker.finished.connect(self._on_export_done)
        self._export_worker.error.connect(self._on_export_error)
        self._export_worker.start()

    def _on_export_done(self, unique_products):
        self.export_btn.setEnabled(True)
        if unique_products is None:
            self.status_text.append("\nכל המוצרים מזוהים!")
            QMessageBox.information(self, "הצלחה", "כל המוצרים מזוהים!")
            return
        from_year, from_month, to_year, to_month = self._export_meta
        filename = f"unidentified_products_{from_year}{from_month:02d}_{to_year}{to_month:02d}.csv"
        unique_products.to_csv(filename, index=False, encoding='utf-8-sig')
        self.status_text.append(f"\nיוצאו {len(unique_products)} מוצרים לקובץ {filename}")
        self.status_text.append("\nהוראות:\n1. פתח ב-Excel\n2. מלא: דרגת מותג / גודל / חומר\n3. שמור וייבא חזרה")
        QMessageBox.information(self, "הצלחה", f"יוצאו {len(unique_products)} מוצרים!\n{filename}")

    def _on_export_error(self, tb):
        self.export_btn.setEnabled(True)
        self.status_text.append(f"\nשגיאה: {tb[:600]}")
        QMessageBox.critical(self, "שגיאה", tb[:500])

    def _import_and_update(self):
        filename, _ = QFileDialog.getOpenFileName(self, "Select CSV", "", "CSV Files (*.csv)")
        if not filename:
            return

        self.status_text.clear()
        self.import_btn.setEnabled(False)

        try:
            df = pd.read_csv(filename, encoding='utf-8-sig')
            self.status_text.append(f"נטענו {len(df)} מוצרים מהקובץ\n")

            from product_identification import LUGGAGE_IDENTIFICATION
            import json

            updates = 0
            for _, row in df.iterrows():
                brand        = str(row['דרגת מותג']).strip()
                size         = str(row['גודל']).strip()
                material     = str(row['חומר']).strip()
                product_desc = str(row['תיאור מוצר']).strip()

                if brand and size and material:
                    category = f"{size} {brand} {material}"
                    if category not in LUGGAGE_IDENTIFICATION:
                        LUGGAGE_IDENTIFICATION[category] = []
                    if product_desc not in LUGGAGE_IDENTIFICATION[category]:
                        LUGGAGE_IDENTIFICATION[category].append(product_desc)
                        updates += 1
                        self.status_text.append(f"הוסף {category}: {product_desc[:50]}...")

            with open('product_identification.py', 'w', encoding='utf-8') as f:
                f.write("# -*- coding: utf-8 -*-\n")
                f.write("import re as _re\n\n")
                f.write(f"LUGGAGE_IDENTIFICATION = {json.dumps(LUGGAGE_IDENTIFICATION, ensure_ascii=False, indent=4)}\n\n")
                f.write("_PATTERNS = sorted(\n")
                f.write("    [(_re.compile(_re.escape(' '.join(desc.split())), _re.IGNORECASE), lt)\n")
                f.write("     for lt, descs in LUGGAGE_IDENTIFICATION.items() for desc in descs],\n")
                f.write("    key=lambda t: -len(t[0].pattern),\n)\n\n")
                f.write("def identify_luggage(product_description):\n")
                f.write("    if not product_description: return None\n")
                f.write("    clean_desc = ' '.join(product_description.split())\n")
                f.write("    for pattern, luggage_type in _PATTERNS:\n")
                f.write("        if pattern.search(clean_desc): return luggage_type\n")
                f.write("    return None\n")

            self.status_text.append(f"\nעודכנו {updates} מוצרים")
            QMessageBox.information(self, "הצלחה",
                f"עודכנו {updates} מוצרים!\nיש להפעיל מחדש את התוכנה.")
        except Exception as e:
            self.status_text.append(f"\nשגיאה: {e}")
            QMessageBox.critical(self, "שגיאה", str(e))

        self.import_btn.setEnabled(True)
