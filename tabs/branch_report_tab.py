# -*- coding: utf-8 -*-
import calendar
from datetime import datetime
import pandas as pd
from qtpy.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QPushButton, QComboBox, QListWidget, QTextEdit, QMessageBox,
)
from qtpy.QtCore import Qt, QThread, Signal as pyqtSignal

from fetch_combined import fetch_with_cache, combine_data


class BranchLoadWorker(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(object)   # pd.DataFrame — combined (סופית only)
    error    = pyqtSignal(str)

    def __init__(self, start_date, end_date):
        super().__init__()
        self.start_date = start_date
        self.end_date   = end_date

    def run(self):
        try:
            self.progress.emit("מושך נתונים מ-Priority…")
            documents, logfile = fetch_with_cache(self.start_date, self.end_date)
            self.progress.emit(f"עיבוד {len(documents)} מסמכים…")
            combined = combine_data(documents, logfile)
            self.finished.emit(combined[combined['סטטוס'] == 'סופית'])
        except Exception as e:
            import traceback; self.error.emit(traceback.format_exc())


class BranchReportWorker(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(str)    # filename
    error    = pyqtSignal(str)

    def __init__(self, combined_data, selected_branches, year, month):
        super().__init__()
        self.combined_data     = combined_data
        self.selected_branches = selected_branches
        self.year  = year
        self.month = month

    def run(self):
        try:
            branches_str = "_".join(self.selected_branches[:2]) if len(self.selected_branches) <= 2 \
                           else f"{len(self.selected_branches)}_branches"
            filename = f"branches_{branches_str}_{self.year}{self.month:02d}.xlsx"
            with pd.ExcelWriter(filename, engine='openpyxl') as writer:
                for branch in self.selected_branches:
                    branch_data = self.combined_data[self.combined_data['סניף'] == branch]
                    if not branch_data.empty:
                        branch_data.to_excel(writer, sheet_name=str(branch)[:31], index=False)
                        self.progress.emit(f"  ✓ {branch}: {len(branch_data)} שורות")
                    else:
                        self.progress.emit(f"  - {branch}: אין נתונים")
            self.finished.emit(filename)
        except Exception as e:
            import traceback; self.error.emit(traceback.format_exc())


class BranchReportTab(QWidget):
    def __init__(self):
        super().__init__()
        self._combined_data = None
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(15)

        title = QLabel("דוחות לפי סניף")
        title.setStyleSheet("font-size: 24px; font-weight: bold; color: #2c3e50;")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        date_group = QGroupBox("בחירת תקופה")
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

        self.load_btn = QPushButton("טען סניפים")
        self.load_btn.setStyleSheet("""
            QPushButton { background-color:#2980b9; color:white; font-size:14px;
                          font-weight:bold; padding:8px 20px; border-radius:6px; }
            QPushButton:hover { background-color:#2471a3; }
        """)
        self.load_btn.clicked.connect(self.load_branches)
        date_layout.addWidget(self.load_btn)
        date_layout.addStretch()

        date_group.setLayout(date_layout)
        layout.addWidget(date_group)

        branch_group = QGroupBox("בחירת סניפים (ניתן לבחור מספר)")
        branch_layout = QVBoxLayout()

        btn_row = QHBoxLayout()
        select_all_btn = QPushButton("בחר הכל")
        select_all_btn.clicked.connect(self._select_all)
        btn_row.addWidget(select_all_btn)
        clear_btn = QPushButton("נקה בחירה")
        clear_btn.clicked.connect(self._clear_selection)
        btn_row.addWidget(clear_btn)
        btn_row.addStretch()
        branch_layout.addLayout(btn_row)

        self.branch_list = QListWidget()
        self.branch_list.setSelectionMode(QListWidget.MultiSelection)
        self.branch_list.setMinimumHeight(160)
        branch_layout.addWidget(self.branch_list)

        self.branch_hint = QLabel("לחץ 'טען סניפים' כדי לאכלס את הרשימה")
        self.branch_hint.setStyleSheet("color:#95a5a6; font-size:12px;")
        self.branch_hint.setAlignment(Qt.AlignCenter)
        branch_layout.addWidget(self.branch_hint)

        branch_group.setLayout(branch_layout)
        layout.addWidget(branch_group)

        self.run_btn = QPushButton("יצירת דוח לסניפים הנבחרים")
        self.run_btn.setEnabled(False)
        self.run_btn.setStyleSheet("""
            QPushButton { background-color:#f39c12; color:white; font-size:18px;
                          font-weight:bold; padding:15px; border-radius:8px; }
            QPushButton:hover { background-color:#e67e22; }
            QPushButton:disabled { background-color:#bdc3c7; }
        """)
        self.run_btn.clicked.connect(self.generate_report)
        layout.addWidget(self.run_btn)

        self.status_text = QTextEdit()
        self.status_text.setReadOnly(True)
        self.status_text.setStyleSheet("background-color:#ecf0f1; font-family:Consolas;")
        layout.addWidget(self.status_text)

        self.setLayout(layout)

    def _select_all(self):
        for i in range(self.branch_list.count()):
            self.branch_list.item(i).setSelected(True)

    def _clear_selection(self):
        self.branch_list.clearSelection()

    def load_branches(self):
        self.status_text.clear()
        self.load_btn.setEnabled(False)
        self.run_btn.setEnabled(False)
        self.branch_list.clear()
        self.branch_hint.setText("טוען נתונים…")

        month = int(self.month_combo.currentText())
        year  = int(self.year_combo.currentText())
        last_day   = calendar.monthrange(year, month)[1]
        start_date = f"{year}-{month:02d}-01"
        end_date   = f"{year}-{month:02d}-{last_day}"

        self._load_worker = BranchLoadWorker(start_date, end_date)
        self._load_worker.progress.connect(self.status_text.append)
        self._load_worker.finished.connect(self._on_load_done)
        self._load_worker.error.connect(self._on_load_error)
        self._load_worker.start()

    def _on_load_done(self, combined):
        self._combined_data = combined
        branches = sorted(combined['סניף'].dropna().unique())
        if not branches:
            self.branch_hint.setText("לא נמצאו סניפים בתקופה זו")
            self.load_btn.setEnabled(True)
            return
        self.branch_list.clear()
        for branch in branches:
            self.branch_list.addItem(str(branch))
        self.branch_hint.setText(f"נמצאו {len(branches)} סניפים — בחר אחד או יותר")
        self.run_btn.setEnabled(True)
        self.status_text.append(f"✓ {len(branches)} סניפים נטענו")
        self.load_btn.setEnabled(True)

    def _on_load_error(self, tb):
        self.branch_hint.setText("שגיאה בטעינת נתונים")
        self.status_text.append(f"✗ שגיאה: {tb[:600]}")
        QMessageBox.critical(self, "שגיאה", tb[:500])
        self.load_btn.setEnabled(True)

    def generate_report(self):
        selected = [item.text() for item in self.branch_list.selectedItems()]
        if not selected:
            QMessageBox.warning(self, "אזהרה", "יש לבחור לפחות סניף אחד"); return

        self.status_text.clear()
        self.run_btn.setEnabled(False)

        month = int(self.month_combo.currentText())
        year  = int(self.year_combo.currentText())

        self._report_worker = BranchReportWorker(self._combined_data, selected, year, month)
        self._report_worker.progress.connect(self.status_text.append)
        self._report_worker.finished.connect(self._on_report_done)
        self._report_worker.error.connect(self._on_report_error)
        self._report_worker.start()

    def _on_report_done(self, filename):
        self.status_text.append(f"\n✓ הקובץ נוצר: {filename}")
        QMessageBox.information(self, "הצלחה", f"הדוח נוצר בהצלחה!\n{filename}")
        self.run_btn.setEnabled(True)

    def _on_report_error(self, tb):
        self.status_text.append(f"\n✗ שגיאה:\n{tb[:800]}")
        QMessageBox.critical(self, "שגיאה", tb[:500])
        self.run_btn.setEnabled(True)
