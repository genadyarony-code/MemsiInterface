# -*- coding: utf-8 -*-
from datetime import datetime
import pandas as pd
from qtpy.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QPushButton, QListWidget, QTableWidget, QTableWidgetItem, QMessageBox,
)
from qtpy.QtCore import Qt, QThread, Signal as pyqtSignal

from logger import logger


class InventoryWorker(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(object)   # pd.DataFrame with 'זיהוי מזוודה' column
    error    = pyqtSignal(str)

    def __init__(self, warehouse_filter):
        super().__init__()
        self.warehouse_filter = warehouse_filter

    def run(self):
        try:
            from inventory_manager import fetch_partbal_inventory
            from domain_repository import identify_luggage

            def _prog(n):
                self.progress.emit(f"נמשכו {n} רשומות…")

            df = fetch_partbal_inventory(self.warehouse_filter, progress_callback=_prog)
            if not df.empty:
                self.progress.emit("מזהה סוגי מוצרים…")
                df = df.copy()
                df['זיהוי מזוודה'] = df['תיאור מוצר'].apply(identify_luggage)
            self.finished.emit(df)
        except Exception:
            import traceback
            logger.exception("InventoryWorker failed")
            self.error.emit(traceback.format_exc())


class InventoryTab(QWidget):
    def __init__(self):
        super().__init__()
        self._current_data = None
        self._init_ui()
        self._load_warehouses()

    def _init_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(12)

        title = QLabel("מעקב מלאי מזוודות")
        title.setStyleSheet("font-size: 24px; font-weight: bold; color: #2c3e50;")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        warehouse_group = QGroupBox("בחירת מחסנים (פעילים ב-3 חודשים האחרונים)")
        warehouse_layout = QVBoxLayout()

        btn_row = QHBoxLayout()
        select_all_btn = QPushButton("בחר הכל")
        select_all_btn.clicked.connect(self._select_all)
        btn_row.addWidget(select_all_btn)
        clear_all_btn = QPushButton("נקה הכל")
        clear_all_btn.clicked.connect(self._clear_all)
        btn_row.addWidget(clear_all_btn)
        btn_row.addStretch()
        warehouse_layout.addLayout(btn_row)

        self.warehouse_list = QListWidget()
        self.warehouse_list.setSelectionMode(QListWidget.MultiSelection)
        self.warehouse_list.setMinimumHeight(180)
        warehouse_layout.addWidget(self.warehouse_list)

        self.wh_hint = QLabel("טוען מחסנים פעילים ממסד הנתונים...")
        self.wh_hint.setStyleSheet("color:#95a5a6; font-size:12px;")
        warehouse_layout.addWidget(self.wh_hint)

        warehouse_group.setLayout(warehouse_layout)
        layout.addWidget(warehouse_group)

        self.generate_btn = QPushButton("הפק דוח מלאי")
        self.generate_btn.setEnabled(False)
        self.generate_btn.setStyleSheet("""
            QPushButton { background-color:#e67e22; color:white; font-size:18px;
                          font-weight:bold; padding:15px; border-radius:8px; }
            QPushButton:hover { background-color:#d35400; }
            QPushButton:disabled { background-color:#bdc3c7; }
        """)
        self.generate_btn.clicked.connect(self._generate_report)
        layout.addWidget(self.generate_btn)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color:#7f8c8d; font-size:12px;")
        self.status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status_label)

        self.table = QTableWidget()
        self.table.setVisible(False)
        layout.addWidget(self.table)

        self.export_btn = QPushButton("ייצא לאקסל")
        self.export_btn.setStyleSheet("""
            QPushButton { background-color:#27ae60; color:white; font-size:16px;
                          font-weight:bold; padding:12px; border-radius:8px; }
            QPushButton:hover { background-color:#229954; }
        """)
        self.export_btn.clicked.connect(self._export_to_excel)
        self.export_btn.setVisible(False)
        layout.addWidget(self.export_btn)

        self.setLayout(layout)

    def _load_warehouses(self):
        try:
            from inventory_manager import get_active_warehouses_from_db
            warehouses = get_active_warehouses_from_db()
            self.warehouse_list.clear()
            for wh in warehouses:
                self.warehouse_list.addItem(wh)
            self.wh_hint.setText(f"נטענו {len(warehouses)} מחסנים פעילים")
            self.generate_btn.setEnabled(True)
        except Exception as e:
            self.wh_hint.setText(f"שגיאה בטעינת מחסנים: {e}")

    def _select_all(self):
        for i in range(self.warehouse_list.count()):
            self.warehouse_list.item(i).setSelected(True)

    def _clear_all(self):
        self.warehouse_list.clearSelection()

    def _generate_report(self):
        selected_items   = self.warehouse_list.selectedItems()
        warehouse_filter = [item.text() for item in selected_items] if selected_items else None

        self.generate_btn.setEnabled(False)
        self.table.setVisible(False)
        self.export_btn.setVisible(False)
        self._current_data = None
        self.status_label.setText("מושך נתוני מלאי מ-Priority…")

        self._worker = InventoryWorker(warehouse_filter)
        self._worker.progress.connect(self.status_label.setText)
        self._worker.finished.connect(self._on_inventory_done)
        self._worker.error.connect(self._on_inventory_error)
        self._worker.start()

    def _on_inventory_done(self, df):
        self.generate_btn.setEnabled(True)
        if df.empty:
            self.status_label.setText("לא נמצא מלאי למחסנים שנבחרו")
            QMessageBox.information(self, "מידע", "לא נמצא מלאי למחסנים שנבחרו")
            return

        self._current_data = df
        df_display = df.reset_index(drop=True)
        # ביצועים: מבטל repaint/sort בזמן מילוי, מאפשר אותם בסוף.
        # על ~10K שורות זה מוריד שניות של רינדור לפחות ממילישנייה.
        self.table.setUpdatesEnabled(False)
        self.table.setSortingEnabled(False)
        try:
            self.table.setRowCount(len(df_display))
            self.table.setColumnCount(len(df_display.columns))
            self.table.setHorizontalHeaderLabels(df_display.columns.tolist())
            values = df_display.values  # numpy ndarray — אינדקס מהיר מ-iloc
            for row_idx in range(len(df_display)):
                for col_idx in range(values.shape[1]):
                    v = values[row_idx, col_idx]
                    self.table.setItem(row_idx, col_idx,
                                       QTableWidgetItem('' if v is None else str(v)))
            self.table.resizeColumnsToContents()
        finally:
            self.table.setSortingEnabled(True)
            self.table.setUpdatesEnabled(True)
        self.table.setVisible(True)
        self.export_btn.setVisible(True)

        identified = df['זיהוי מזוודה'].notna().sum()
        self.status_label.setText(
            f"✓ {len(df)} שורות | מזוהים: {identified} | לא מזוהים: {len(df) - identified}")

    def _on_inventory_error(self, tb):
        self.generate_btn.setEnabled(True)
        self.status_label.setText("שגיאה בשליפת מלאי")
        QMessageBox.critical(self, "שגיאה", tb[:500])

    def _export_to_excel(self):
        if self._current_data is None or self._current_data.empty:
            return
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename  = f"inventory_report_{timestamp}.xlsx"
        try:
            df = self._current_data.reset_index(drop=True)
            warehouses      = sorted(df['מחסן'].unique())
            identifications = sorted(df['זיהוי מזוודה'].dropna().unique())

            summary_rows = []
            for ident in identifications:
                row = {'זיהוי מזוודה': ident}
                subset = df[df['זיהוי מזוודה'] == ident]
                for wh in warehouses:
                    row[f'מחסן {wh}'] = int(subset[subset['מחסן'] == wh]['יתרה'].sum())
                row['סה"כ'] = int(subset['יתרה'].sum())
                summary_rows.append(row)

            unidentified = df[df['זיהוי מזוודה'].isna()]
            if not unidentified.empty:
                row = {'זיהוי מזוודה': '— לא מזוהה —'}
                for wh in warehouses:
                    row[f'מחסן {wh}'] = int(unidentified[unidentified['מחסן'] == wh]['יתרה'].sum())
                row['סה"כ'] = int(unidentified['יתרה'].sum())
                summary_rows.append(row)

            summary_df = pd.DataFrame(summary_rows)
            with pd.ExcelWriter(filename, engine='openpyxl') as writer:
                df.to_excel(writer, sheet_name='פירוט', index=False)
                summary_df.to_excel(writer, sheet_name='סיכום', index=False)

            QMessageBox.information(self, "הצלחה", f"הקובץ נוצר בהצלחה!\n{filename}")
        except Exception as e:
            QMessageBox.critical(self, "שגיאה", f"שגיאה בייצוא: {e}")
