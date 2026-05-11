# -*- coding: utf-8 -*-
"""
sync_dialog.py — דיאלוגי-progress לסנכרון.

שני סוגים:
- SmallSyncDialog: בפינה הימנית-תחתונה, לא חוסם. נפתח כשעבר <24 שעות.
- BigSyncDialog: מודלי במרכז המסך, חוסם. נפתח כשעבר >=24 שעות (יום מלא חלף).

שניהם מציגים את אותו progress + step names + סטטוס סופי. הם רק שונים
בגודל, מיקום, ומדיניות "חוסם או לא".
"""
from __future__ import annotations
from qtpy.QtCore import Qt, QPoint
from qtpy.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar,
    QPushButton, QWidget,
)


# שמות-עברית לשלבים, ל-UI
_STEP_LABELS = {
    'priority_rolling': 'מושך נתונים מ-Priority...',
    'partbal':          'מעדכן תמונת מלאי...',
    'iaa':              'בודק נתוני נחיתות חדשים...',
}


def _format_pulled(pulled: dict) -> str:
    parts = []
    if 'documents' in pulled:
        parts.append(f"{pulled['documents']} מסמכים")
    if 'logfile' in pulled:
        parts.append(f"{pulled['logfile']} תנועות")
    if 'partbal_rows' in pulled:
        parts.append(f"{pulled['partbal_rows']:,} פריטי מלאי")
    if pulled.get('iaa_months_synced'):
        parts.append(f"{pulled['iaa_months_synced']} חודשי IAA")
    return ' • '.join(parts) if parts else 'אין נתונים חדשים'


class _BaseSyncDialog(QDialog):
    """משותף לשני הסוגים. כותרת + status label + progress bar + close button."""

    def __init__(self, parent, worker):
        super().__init__(parent)
        self.worker = worker
        self.setWindowTitle("מעדכן מסד נתונים")
        self._auto_close = True
        self._build_ui()
        self._wire_worker()

    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setSpacing(10)
        v.setContentsMargins(20, 16, 20, 16)

        self.title_lbl = QLabel("מעדכן מסד נתונים מעדכון אחרון")
        self.title_lbl.setStyleSheet("font-size:14px; font-weight:bold; color:#2c3e50;")
        v.addWidget(self.title_lbl)

        self.status_lbl = QLabel("מתחבר...")
        self.status_lbl.setStyleSheet("font-size:12px; color:#34495e;")
        v.addWidget(self.status_lbl)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # indeterminate
        v.addWidget(self.progress)

        # שורת פעולות
        h = QHBoxLayout()
        h.addStretch()
        self.close_btn = QPushButton("סגור")
        self.close_btn.setEnabled(False)
        self.close_btn.clicked.connect(self.accept)
        h.addWidget(self.close_btn)
        v.addLayout(h)

    def _wire_worker(self):
        self.worker.step_started.connect(self._on_step_started)
        self.worker.step_done.connect(self._on_step_done)
        self.worker.finished_ok.connect(self._on_ok)
        self.worker.finished_partial.connect(self._on_partial)
        self.worker.finished_failed.connect(self._on_failed)

    def _on_step_started(self, name: str):
        self.status_lbl.setText(_STEP_LABELS.get(name, name))

    def _on_step_done(self, name: str, result: dict):
        pass  # status מתעדכן בשלב הבא, או בסיום

    def _on_ok(self, pulled: dict):
        self.title_lbl.setText("הסנכרון הסתיים בהצלחה")
        self.title_lbl.setStyleSheet("font-size:14px; font-weight:bold; color:#27ae60;")
        self.status_lbl.setText(_format_pulled(pulled))
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.close_btn.setEnabled(True)
        if self._auto_close:
            # סוגר אוטומטית אחרי 2 שניות אם הצליח חלק
            from qtpy.QtCore import QTimer
            QTimer.singleShot(2000, self.accept)

    def _on_partial(self, pulled: dict, errors: str):
        self.title_lbl.setText("הסנכרון הסתיים חלקית")
        self.title_lbl.setStyleSheet("font-size:14px; font-weight:bold; color:#e67e22;")
        self.status_lbl.setText(
            f"{_format_pulled(pulled)}\nשגיאות:\n{errors[:300]}"
        )
        self.status_lbl.setWordWrap(True)
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.close_btn.setEnabled(True)
        self._auto_close = False  # תן למשתמש לקרוא

    def _on_failed(self, error: str):
        self.title_lbl.setText("הסנכרון נכשל")
        self.title_lbl.setStyleSheet("font-size:14px; font-weight:bold; color:#e74c3c;")
        self.status_lbl.setText(error[:300])
        self.status_lbl.setWordWrap(True)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.close_btn.setEnabled(True)
        self._auto_close = False


class SmallSyncDialog(_BaseSyncDialog):
    """דיאלוג קטן בפינה הימנית-תחתונה. לא חוסם — המשתמש יכול לעבוד.

    מוצג כשעבר <24 שעות מהסנכרון האחרון: אז זה מהיר ולא קריטי.
    """

    def __init__(self, parent, worker):
        super().__init__(parent, worker)
        self.setWindowFlags(Qt.Tool | Qt.WindowStaysOnTopHint |
                            Qt.FramelessWindowHint)
        self.setModal(False)
        self.setFixedSize(360, 130)
        self.setStyleSheet(
            "QDialog{background:#ecf0f1; border:1px solid #95a5a6; border-radius:6px;}"
        )

    def showEvent(self, event):
        super().showEvent(event)
        # ממקם בפינה הימנית-תחתונה של המסך-של-ה-app
        if self.parent():
            geo = self.parent().geometry()
            x = geo.right() - self.width() - 20
            y = geo.bottom() - self.height() - 40
            self.move(QPoint(x, y))


class BigSyncDialog(_BaseSyncDialog):
    """דיאלוג מרכזי וחוסם, ל-startup אחרי 24+ שעות.

    יום שלם עבר, יש סיכוי לשינויים גדולים. המשתמש מחכה לסיום לפני שהוא
    מתחיל לעבוד — מבטיח שהנתונים שמוצגים מעודכנים.
    """

    def __init__(self, parent, worker):
        super().__init__(parent, worker)
        self.setModal(True)
        self.setFixedSize(480, 200)
        self.title_lbl.setText("מעדכן מסד נתונים מעדכון אחרון, נא להמתין")

    def _on_ok(self, pulled: dict):
        super()._on_ok(pulled)
        # ב-Big לא לסגור אוטומטית — תן למשתמש לראות שהסתיים
        self._auto_close = False
