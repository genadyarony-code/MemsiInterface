# -*- coding: utf-8 -*-
"""
updates_tab.py — עריכת מחירונים, סניפים, וזיהוי מזוודות.

כותב ישירות ל-DB דרך domain_repository, עם audit log.
"""
import json
import pandas as pd
from datetime import datetime
from qtpy.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QPushButton, QComboBox, QLineEdit, QDoubleSpinBox, QSpinBox,
    QMessageBox,
    QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QSplitter, QAbstractItemView, QCheckBox,
)
from qtpy.QtCore import Qt
from qtpy.QtGui import QFont, QColor

import domain_repository as repo
from logger import logger


# ============================================================
#  Helpers
# ============================================================
def _styled_table(headers: list[str], header_bg: str = '#2c3e50') -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setEditTriggers(QTableWidget.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setAlternatingRowColors(True)
    t.horizontalHeader().setStretchLastSection(True)
    t.setStyleSheet(
        "QTableWidget{font-size:12px;}"
        f"QHeaderView::section{{font-weight:bold;background:{header_bg};"
        "color:white;padding:4px;}"
    )
    return t


def _fill_table(t: QTableWidget, rows: list[list]):
    t.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            item = QTableWidgetItem(str(val) if val is not None else '')
            item.setTextAlignment(Qt.AlignCenter)
            t.setItem(r, c, item)
    t.resizeColumnsToContents()


def _save_button(text: str = "שמור עדכון") -> QPushButton:
    btn = QPushButton(text)
    btn.setStyleSheet("""
        QPushButton { background-color:#27ae60; color:white; font-size:14px;
                      font-weight:bold; padding:8px 18px; border-radius:6px; }
        QPushButton:hover { background-color:#229954; }
        QPushButton:disabled { background-color:#bdc3c7; }
    """)
    return btn


# ============================================================
#  Section: customer repair prices
# ============================================================
class _CustomerRepairForm(QWidget):
    def __init__(self, parent_tab):
        super().__init__()
        self.parent_tab = parent_tab
        v = QVBoxLayout(self); v.setSpacing(8)

        # form
        form = QHBoxLayout()
        form.addWidget(QLabel("Tier:"))
        self.tier = QComboBox()
        form.addWidget(self.tier)
        form.addWidget(QLabel('מק"ט:'))
        self.sku = QComboBox()
        self.sku.setEditable(True)
        self.sku.setMinimumWidth(140)
        form.addWidget(self.sku)
        form.addWidget(QLabel("מחיר:"))
        self.price = QDoubleSpinBox()
        self.price.setMaximum(99999); self.price.setDecimals(2)
        form.addWidget(self.price)
        save = _save_button()
        save.clicked.connect(self._save)
        form.addWidget(save)
        form.addStretch()
        v.addLayout(form)

        # table
        self.table = _styled_table(
            ["Tier", 'מק"ט', "מחיר", "עודכן ע\"י", "עודכן בתאריך"],
            header_bg='#3498db',
        )
        self.table.cellClicked.connect(self._row_to_form)
        v.addWidget(self.table)

        self._reload_options()
        self._reload_table()

    def _reload_options(self):
        self.tier.clear()
        self.tier.addItems(repo.list_pricing_tiers())
        self.sku.clear()
        self.sku.addItems(repo.list_repair_part_skus())

    def _reload_table(self):
        from db_config import get_conn
        rows = []
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT pricing_tier, part_sku, price, updated_by, updated_at
                FROM customer_repair_prices
                ORDER BY pricing_tier, part_sku
            """)
            for tier, sku, price, by, at in cur.fetchall():
                rows.append([tier, sku, f"{float(price):.2f}", by or '',
                             at.strftime("%Y-%m-%d %H:%M") if at else ''])
        _fill_table(self.table, rows)

    def _row_to_form(self, r, _c):
        self.tier.setCurrentText(self.table.item(r, 0).text())
        self.sku.setEditText(self.table.item(r, 1).text())
        try:
            self.price.setValue(float(self.table.item(r, 2).text()))
        except ValueError:
            pass

    def _save(self):
        tier = self.tier.currentText().strip()
        sku = self.sku.currentText().strip()
        price = float(self.price.value())
        if not tier or not sku:
            QMessageBox.warning(self, "שגיאה", "Tier ומק\"ט הם שדות חובה")
            return
        try:
            repo.upsert_customer_repair_price(tier, sku, price,
                                              user=repo.get_current_user())
            self._reload_table()
            self.parent_tab.refresh_audit()
            QMessageBox.information(self, "✓", f"מחיר תיקון עודכן: {tier}/{sku} = {price:.2f}")
        except Exception as e:
            logger.exception("save customer_repair_price failed")
            QMessageBox.critical(self, "שגיאה", f"{type(e).__name__}: {e}")


# ============================================================
#  Section: customer replacement prices
# ============================================================
class _CustomerReplacementForm(QWidget):
    def __init__(self, parent_tab):
        super().__init__()
        self.parent_tab = parent_tab
        v = QVBoxLayout(self); v.setSpacing(8)

        form = QHBoxLayout()
        form.addWidget(QLabel("Tier:"))
        self.tier = QComboBox(); form.addWidget(self.tier)
        form.addWidget(QLabel("סוג מזוודה:"))
        self.luggage = QComboBox(); self.luggage.setMinimumWidth(180)
        form.addWidget(self.luggage)
        form.addWidget(QLabel("מחיר:"))
        self.price = QDoubleSpinBox()
        self.price.setMaximum(99999); self.price.setDecimals(2)
        form.addWidget(self.price)
        save = _save_button(); save.clicked.connect(self._save)
        form.addWidget(save)
        form.addStretch()
        v.addLayout(form)

        self.table = _styled_table(
            ["Tier", "סוג מזוודה", "מחיר", "עודכן ע\"י", "עודכן בתאריך"],
            header_bg='#27ae60',
        )
        self.table.cellClicked.connect(self._row_to_form)
        v.addWidget(self.table)

        self._reload_options()
        self._reload_table()

    def _reload_options(self):
        self.tier.clear(); self.tier.addItems(repo.list_pricing_tiers())
        self.luggage.clear(); self.luggage.addItems(repo.list_luggage_categories())

    def _reload_table(self):
        from db_config import get_conn
        rows = []
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT pricing_tier, luggage_type, price, updated_by, updated_at
                FROM customer_replacement_prices
                ORDER BY pricing_tier, luggage_type
            """)
            for tier, lt, price, by, at in cur.fetchall():
                rows.append([tier, lt, f"{float(price):.2f}", by or '',
                             at.strftime("%Y-%m-%d %H:%M") if at else ''])
        _fill_table(self.table, rows)

    def _row_to_form(self, r, _c):
        self.tier.setCurrentText(self.table.item(r, 0).text())
        self.luggage.setCurrentText(self.table.item(r, 1).text())
        try:
            self.price.setValue(float(self.table.item(r, 2).text()))
        except ValueError:
            pass

    def _save(self):
        tier = self.tier.currentText().strip()
        lt = self.luggage.currentText().strip()
        price = float(self.price.value())
        if not tier or not lt:
            QMessageBox.warning(self, "שגיאה", "Tier וסוג מזוודה הם שדות חובה")
            return
        try:
            repo.upsert_customer_replacement_price(
                tier, lt, price, user=repo.get_current_user())
            self._reload_table()
            self.parent_tab.refresh_audit()
            QMessageBox.information(self, "✓", f"מחיר החלפה עודכן: {tier}/{lt} = {price:.2f}")
        except Exception as e:
            logger.exception("save customer_replacement_price failed")
            QMessageBox.critical(self, "שגיאה", f"{type(e).__name__}: {e}")


# ============================================================
#  Section: supplier prices (repair + replacement together)
# ============================================================
class _SupplierForm(QWidget):
    def __init__(self, parent_tab):
        super().__init__()
        self.parent_tab = parent_tab
        v = QVBoxLayout(self); v.setSpacing(8)

        form = QHBoxLayout()
        form.addWidget(QLabel("סוג:"))
        self.kind = QComboBox()
        self.kind.addItems(["תיקון", "החלפה"])
        self.kind.currentIndexChanged.connect(self._kind_changed)
        form.addWidget(self.kind)
        form.addWidget(QLabel("פריט:"))
        self.item = QComboBox()
        self.item.setEditable(True)
        self.item.setMinimumWidth(180)
        form.addWidget(self.item)
        form.addWidget(QLabel("מחיר:"))
        self.price = QDoubleSpinBox()
        self.price.setMaximum(99999); self.price.setDecimals(2)
        form.addWidget(self.price)
        save = _save_button(); save.clicked.connect(self._save)
        form.addWidget(save)
        form.addStretch()
        v.addLayout(form)

        self.table = _styled_table(
            ["סוג", "פריט", "מחיר", "עודכן ע\"י", "עודכן בתאריך"],
            header_bg='#e67e22',
        )
        self.table.cellClicked.connect(self._row_to_form)
        v.addWidget(self.table)

        self._kind_changed()
        self._reload_table()

    def _kind_changed(self):
        self.item.clear()
        if self.kind.currentText() == "תיקון":
            self.item.addItems(repo.list_repair_part_skus())
        else:
            self.item.addItems(repo.list_luggage_categories())

    def _reload_table(self):
        from db_config import get_conn
        rows = []
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT 'תיקון', part_sku, price, updated_by, updated_at
                FROM supplier_repair_prices
                UNION ALL
                SELECT 'החלפה', luggage_type, price, updated_by, updated_at
                FROM supplier_replacement_prices
                ORDER BY 1, 2
            """)
            for kind, item, price, by, at in cur.fetchall():
                rows.append([kind, item, f"{float(price):.2f}", by or '',
                             at.strftime("%Y-%m-%d %H:%M") if at else ''])
        _fill_table(self.table, rows)

    def _row_to_form(self, r, _c):
        kind_in_row = self.table.item(r, 0).text()
        self.kind.setCurrentText(kind_in_row)
        self.item.setEditText(self.table.item(r, 1).text())
        try:
            self.price.setValue(float(self.table.item(r, 2).text()))
        except ValueError:
            pass

    def _save(self):
        kind = self.kind.currentText()
        item = self.item.currentText().strip()
        price = float(self.price.value())
        if not item:
            QMessageBox.warning(self, "שגיאה", "פריט הוא שדה חובה")
            return
        try:
            user = repo.get_current_user()
            if kind == "תיקון":
                repo.upsert_supplier_repair_price(item, price, user=user)
            else:
                repo.upsert_supplier_replacement_price(item, price, user=user)
            self._reload_table()
            self.parent_tab.refresh_audit()
            QMessageBox.information(self, "✓", f"מחיר ספק ({kind}): {item} = {price:.2f}")
        except Exception as e:
            logger.exception("save supplier price failed")
            QMessageBox.critical(self, "שגיאה", f"{type(e).__name__}: {e}")


# ============================================================
#  Section: luggage identification
# ============================================================
class _LuggageIdForm(QWidget):
    def __init__(self, parent_tab):
        super().__init__()
        self.parent_tab = parent_tab
        v = QVBoxLayout(self); v.setSpacing(8)

        form = QHBoxLayout()
        form.addWidget(QLabel("קטגוריה:"))
        self.category = QComboBox()
        self.category.setEditable(True)
        self.category.setMinimumWidth(180)
        form.addWidget(self.category)
        form.addWidget(QLabel("תיאור מוצר:"))
        self.description = QLineEdit()
        self.description.setPlaceholderText("הכנס תיאור מוצר מדויק")
        self.description.setMinimumWidth(360)
        form.addWidget(self.description, 1)
        save = _save_button(); save.clicked.connect(self._save)
        form.addWidget(save)
        v.addLayout(form)

        # search
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("חיפוש:"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("חפש לפי תיאור או קטגוריה")
        self.search.textChanged.connect(self._apply_filter)
        search_row.addWidget(self.search, 1)
        v.addLayout(search_row)

        self.table = _styled_table(
            ["קטגוריה", "תיאור מוצר", "עודכן ע\"י", "עודכן בתאריך"],
            header_bg='#16a085',
        )
        self.table.cellClicked.connect(self._row_to_form)
        v.addWidget(self.table)

        self._all_rows: list[list] = []
        self._reload_options()
        self._reload_table()

    def _reload_options(self):
        self.category.clear()
        self.category.addItems(repo.list_luggage_categories())

    def _reload_table(self):
        from db_config import get_conn
        rows = []
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT category, description, updated_by, updated_at
                FROM luggage_identification
                ORDER BY category, description
            """)
            for cat, desc, by, at in cur.fetchall():
                rows.append([cat, desc, by or '',
                             at.strftime("%Y-%m-%d %H:%M") if at else ''])
        self._all_rows = rows
        self._apply_filter()

    def _apply_filter(self):
        q = self.search.text().strip().lower() if hasattr(self, 'search') else ''
        if q:
            rows = [r for r in self._all_rows
                    if q in str(r[0]).lower() or q in str(r[1]).lower()]
        else:
            rows = self._all_rows
        _fill_table(self.table, rows)

    def _row_to_form(self, r, _c):
        self.category.setCurrentText(self.table.item(r, 0).text())
        self.description.setText(self.table.item(r, 1).text())

    def _save(self):
        cat = self.category.currentText().strip()
        desc = self.description.text().strip()
        if not cat or not desc:
            QMessageBox.warning(self, "שגיאה", "קטגוריה ותיאור הם שדות חובה")
            return
        try:
            repo.add_luggage_identification(desc, cat,
                                            user=repo.get_current_user())
            self._reload_options()
            self._reload_table()
            self.parent_tab.refresh_audit()
            self.description.clear()
            QMessageBox.information(self, "✓", f"זיהוי מזוודה נוסף: '{desc}' → {cat}")
        except Exception as e:
            logger.exception("add luggage_identification failed")
            QMessageBox.critical(self, "שגיאה", f"{type(e).__name__}: {e}")


# ============================================================
#  Section: forecast_events editor (חגים, מלחמה, מבצעים…)
# ============================================================
class _ForecastEventsForm(QWidget):
    TRAVEL_OPTIONS = ['collapse', 'very_low', 'low', 'recovering', 'normal', 'high']

    def __init__(self, parent_tab):
        super().__init__()
        self.parent_tab = parent_tab
        v = QVBoxLayout(self); v.setSpacing(8)

        # form
        form = QHBoxLayout()
        form.addWidget(QLabel("חודש:"))
        self.year_month = QLineEdit()
        self.year_month.setPlaceholderText("YYYY-MM")
        self.year_month.setMaximumWidth(110)
        form.addWidget(self.year_month)

        self.cb_war   = QCheckBox("מלחמה")
        self.cb_op    = QCheckBox("מבצע צבאי")
        self.cb_cease = QCheckBox("הפסקת אש")
        self.cb_summer = QCheckBox("שיא קיץ")
        for cb in (self.cb_war, self.cb_op, self.cb_cease, self.cb_summer):
            form.addWidget(cb)

        form.addWidget(QLabel("חג:"))
        self.holiday = QSpinBox()
        self.holiday.setRange(0, 5)
        self.holiday.setToolTip("0=ללא, 1=פסח, 2=ר\"ה/סוכות, 3+=אחר")
        form.addWidget(self.holiday)

        form.addWidget(QLabel("עונה:"))
        self.season = QSpinBox(); self.season.setRange(0, 4)
        self.season.setToolTip("1=חורף, 2=אביב, 3=קיץ, 4=סתיו")
        form.addWidget(self.season)

        form.addWidget(QLabel("travel_impact:"))
        self.travel = QComboBox()
        self.travel.addItems(self.TRAVEL_OPTIONS)
        self.travel.setCurrentText('normal')
        form.addWidget(self.travel)

        save = _save_button(); save.clicked.connect(self._save)
        form.addWidget(save)
        v.addLayout(form)

        # notes
        notes_row = QHBoxLayout()
        notes_row.addWidget(QLabel("הערות:"))
        self.notes = QLineEdit()
        notes_row.addWidget(self.notes, 1)
        v.addLayout(notes_row)

        # table
        self.table = _styled_table(
            ["חודש", "מלחמה", "מבצע", "הפ.אש", "חג", "עונה",
             "שיא קיץ", "travel", "הערות"],
            header_bg='#9b59b6',
        )
        self.table.cellClicked.connect(self._row_to_form)
        v.addWidget(self.table)

        self._reload_table()

    def _open_db(self):
        from forecast_db import ForecastDB
        return ForecastDB()

    def _reload_table(self):
        try:
            db = self._open_db()
            df = db.get_events()
            db.close()
        except Exception as e:
            logger.exception("forecast_events load failed")
            df = pd.DataFrame()
        rows = []
        for _, row in df.iterrows():
            rows.append([
                row.get('year_month', ''),
                int(row.get('is_war', 0) or 0),
                int(row.get('is_military_op', 0) or 0),
                int(row.get('is_ceasefire', 0) or 0),
                int(row.get('jewish_holiday', 0) or 0),
                int(row.get('season', 0) or 0),
                int(row.get('is_summer_peak', 0) or 0),
                row.get('travel_impact', '') or '',
                (row.get('notes', '') or '')[:80],
            ])
        # newest first
        rows.sort(key=lambda r: r[0], reverse=True)
        _fill_table(self.table, rows)

    def _row_to_form(self, r, _c):
        self.year_month.setText(self.table.item(r, 0).text())
        self.cb_war.setChecked(self.table.item(r, 1).text() == '1')
        self.cb_op.setChecked(self.table.item(r, 2).text() == '1')
        self.cb_cease.setChecked(self.table.item(r, 3).text() == '1')
        try: self.holiday.setValue(int(self.table.item(r, 4).text()))
        except ValueError: self.holiday.setValue(0)
        try: self.season.setValue(int(self.table.item(r, 5).text()))
        except ValueError: self.season.setValue(0)
        self.cb_summer.setChecked(self.table.item(r, 6).text() == '1')
        self.travel.setCurrentText(self.table.item(r, 7).text() or 'normal')
        self.notes.setText(self.table.item(r, 8).text())

    def _save(self):
        ym = self.year_month.text().strip()
        if not ym or len(ym) != 7 or ym[4] != '-':
            QMessageBox.warning(self, "שגיאה", "פורמט חודש לא תקין. דוגמה: 2026-04")
            return
        try:
            db = self._open_db()
            db.upsert_event(
                ym,
                is_war=int(self.cb_war.isChecked()),
                is_military_op=int(self.cb_op.isChecked()),
                is_ceasefire=int(self.cb_cease.isChecked()),
                jewish_holiday=int(self.holiday.value()),
                season=int(self.season.value()),
                is_summer_peak=int(self.cb_summer.isChecked()),
                travel_impact=self.travel.currentText(),
                notes=self.notes.text().strip(),
            )
            db.close()
            self._reload_table()
            QMessageBox.information(self, "✓", f"אירוע {ym} עודכן")
        except Exception as e:
            logger.exception("forecast_events save failed")
            QMessageBox.critical(self, "שגיאה", f"{type(e).__name__}: {e}")


# ============================================================
#  Audit log viewer
# ============================================================
class _AuditPanel(QWidget):
    TABLE_LABELS = {
        'customer_repair_prices':      'מחיר תיקון ללקוח',
        'customer_replacement_prices': 'מחיר החלפה ללקוח',
        'supplier_repair_prices':      'מחיר תיקון לספק',
        'supplier_replacement_prices': 'מחיר החלפה לספק',
        'luggage_identification':      'זיהוי מזוודה',
    }

    def __init__(self):
        super().__init__()
        v = QVBoxLayout(self); v.setSpacing(4); v.setContentsMargins(0, 0, 0, 0)
        title = QLabel("היסטוריית שינויים אחרונים")
        title.setStyleSheet("font-weight:bold;font-size:13px;color:#2c3e50;")
        v.addWidget(title)

        self.table = _styled_table(
            ["מתי", "מי", "טבלה", "פעולה", "מפתח", "ערך ישן", "ערך חדש"],
            header_bg='#7f8c8d',
        )
        self.table.setMaximumHeight(180)
        v.addWidget(self.table)

    def reload(self):
        try:
            entries = repo.get_recent_audit(limit=50)
        except Exception as e:
            logger.exception("get_recent_audit failed")
            return
        rows = []
        for e in entries:
            key = e.get('key_json') or {}
            old = e.get('old_values') or {}
            new = e.get('new_values') or {}
            rows.append([
                e['changed_at'].strftime("%Y-%m-%d %H:%M") if e.get('changed_at') else '',
                e.get('changed_by') or '',
                self.TABLE_LABELS.get(e['table_name'], e['table_name']),
                e['operation'],
                self._fmt_dict(key),
                self._fmt_dict(old),
                self._fmt_dict(new),
            ])
        _fill_table(self.table, rows)

    @staticmethod
    def _fmt_dict(d) -> str:
        if not d:
            return ''
        if isinstance(d, str):
            try:
                d = json.loads(d)
            except Exception:
                return d
        return ', '.join(f"{k}={v}" for k, v in d.items())


# ============================================================
#  Main UpdatesTab
# ============================================================
class UpdatesTab(QWidget):
    def __init__(self):
        super().__init__()
        self._init_ui()

    def _init_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)

        title = QLabel("עדכון מסדי נתונים")
        title.setStyleSheet("font-size: 22px; font-weight: bold; color: #2c3e50;")
        title.setAlignment(Qt.AlignCenter)
        outer.addWidget(title)

        # Splitter: top = sub-tabs, bottom = audit log
        splitter = QSplitter(Qt.Vertical)

        sub = QTabWidget()
        sub.setStyleSheet(
            "QTabBar::tab{font-size:13px;padding:6px 14px;}"
            "QTabBar::tab:selected{font-weight:bold;color:#2980b9;}"
        )
        sub.addTab(_CustomerRepairForm(self),      "מחיר תיקון ללקוח")
        sub.addTab(_CustomerReplacementForm(self), "מחיר החלפה ללקוח")
        sub.addTab(_SupplierForm(self),            "מחירי ספקים")
        sub.addTab(_LuggageIdForm(self),           "זיהוי מזוודה")
        sub.addTab(_ForecastEventsForm(self),      "אירועי תחזית")
        splitter.addWidget(sub)

        self.audit = _AuditPanel()
        splitter.addWidget(self.audit)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        outer.addWidget(splitter, 1)

        self.refresh_audit()

    def refresh_audit(self):
        self.audit.reload()
