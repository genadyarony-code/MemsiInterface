# -*- coding: utf-8 -*-
from qtpy.QtWidgets import (
    QWidget, QVBoxLayout, QGroupBox, QLabel,
    QPushButton, QComboBox, QLineEdit, QDoubleSpinBox, QMessageBox,
)
from qtpy.QtCore import Qt

from pricing_data import (
    CUSTOMER_REPAIR_PRICING, CUSTOMER_REPLACEMENT_PRICING, SUPPLIER_REPLACEMENT_PRICING,
)
from product_identification import LUGGAGE_IDENTIFICATION


class UpdatesTab(QWidget):
    def __init__(self):
        super().__init__()
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout()

        title = QLabel("עדכון מסדי נתונים")
        title.setStyleSheet("font-size: 24px; font-weight: bold; color: #2c3e50;")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        type_group = QGroupBox("סוג עדכון")
        type_layout = QVBoxLayout()
        self.update_type = QComboBox()
        self.update_type.addItems([
            "הוספת מזוודה למאגר זיהוי",
            "עדכון מחיר תיקון ללקוח",
            "עדכון מחיר החלפה ללקוח",
            "עדכון מחיר תשלום לספק",
        ])
        self.update_type.currentIndexChanged.connect(self._update_form)
        type_layout.addWidget(self.update_type)
        type_group.setLayout(type_layout)
        layout.addWidget(type_group)

        self.form_widget = QWidget()
        self.form_layout = QVBoxLayout()
        self.form_widget.setLayout(self.form_layout)
        layout.addWidget(self.form_widget)

        self.save_btn = QPushButton("שמור עדכון")
        self.save_btn.setStyleSheet("""
            QPushButton { background-color:#27ae60; color:white; font-size:16px;
                          font-weight:bold; padding:12px; border-radius:8px; }
            QPushButton:hover { background-color:#229954; }
        """)
        self.save_btn.clicked.connect(self._save_update)
        layout.addWidget(self.save_btn)

        layout.addStretch()
        self.setLayout(layout)
        self._update_form()

    def _update_form(self):
        for i in reversed(range(self.form_layout.count())):
            self.form_layout.itemAt(i).widget().setParent(None)

        idx = self.update_type.currentIndex()

        if idx == 0:
            self.form_layout.addWidget(QLabel("קטגוריית מזוודה:"))
            self.luggage_category = QComboBox()
            self.luggage_category.addItems(list(LUGGAGE_IDENTIFICATION.keys()))
            self.form_layout.addWidget(self.luggage_category)
            self.form_layout.addWidget(QLabel("תיאור מוצר:"))
            self.product_desc = QLineEdit()
            self.product_desc.setPlaceholderText("הכנס תיאור מוצר מדויק")
            self.form_layout.addWidget(self.product_desc)

        elif idx == 1:
            self.form_layout.addWidget(QLabel("לקוח:"))
            self.customer_select = QComboBox()
            self.customer_select.addItems(list(CUSTOMER_REPAIR_PRICING.keys()))
            self.form_layout.addWidget(self.customer_select)
            self.form_layout.addWidget(QLabel("מק\"ט:"))
            self.part_code = QLineEdit()
            self.part_code.setPlaceholderText("לדוגמה: 900000101")
            self.form_layout.addWidget(self.part_code)
            self.form_layout.addWidget(QLabel("מחיר:"))
            self.price_input = QDoubleSpinBox()
            self.price_input.setMaximum(10000)
            self.form_layout.addWidget(self.price_input)

        elif idx == 2:
            self.form_layout.addWidget(QLabel("לקוח:"))
            self.customer_select = QComboBox()
            self.customer_select.addItems(list(CUSTOMER_REPLACEMENT_PRICING.keys()))
            self.form_layout.addWidget(self.customer_select)
            self.form_layout.addWidget(QLabel("סוג מזוודה:"))
            self.luggage_type = QComboBox()
            self.luggage_type.addItems(list(LUGGAGE_IDENTIFICATION.keys()))
            self.form_layout.addWidget(self.luggage_type)
            self.form_layout.addWidget(QLabel("מחיר:"))
            self.price_input = QDoubleSpinBox()
            self.price_input.setMaximum(10000)
            self.form_layout.addWidget(self.price_input)

        elif idx == 3:
            self.form_layout.addWidget(QLabel("סוג:"))
            self.supplier_type = QComboBox()
            self.supplier_type.addItems(["תיקון", "החלפה"])
            self.supplier_type.currentIndexChanged.connect(self._update_supplier_form)
            self.form_layout.addWidget(self.supplier_type)
            self.supplier_detail_widget = QWidget()
            self.supplier_detail_layout = QVBoxLayout()
            self.supplier_detail_widget.setLayout(self.supplier_detail_layout)
            self.form_layout.addWidget(self.supplier_detail_widget)
            self._update_supplier_form()

    def _update_supplier_form(self):
        for i in reversed(range(self.supplier_detail_layout.count())):
            self.supplier_detail_layout.itemAt(i).widget().setParent(None)
        if self.supplier_type.currentText() == "תיקון":
            self.supplier_detail_layout.addWidget(QLabel("מק\"ט:"))
            self.supplier_code = QLineEdit()
            self.supplier_code.setPlaceholderText("לדוגמה: 900000101")
            self.supplier_detail_layout.addWidget(self.supplier_code)
        else:
            self.supplier_detail_layout.addWidget(QLabel("סוג מזוודה:"))
            self.supplier_luggage = QComboBox()
            self.supplier_luggage.addItems(list(SUPPLIER_REPLACEMENT_PRICING.keys()))
            self.supplier_detail_layout.addWidget(self.supplier_luggage)
        self.supplier_detail_layout.addWidget(QLabel("מחיר:"))
        self.supplier_price = QDoubleSpinBox()
        self.supplier_price.setMaximum(10000)
        self.supplier_detail_layout.addWidget(self.supplier_price)

    def _save_update(self):
        QMessageBox.information(self, "הצלחה",
            "העדכון נשמר בהצלחה!\n(פונקציונליות זו תדרוש שמירה לקבצי Python)")
