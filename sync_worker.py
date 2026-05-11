# -*- coding: utf-8 -*-
"""
sync_worker.py — QThread worker שמריץ את nightly_sync ב-background.

המנגנון: ה-GUI מציג דיאלוג עם progress. ה-worker רץ ב-thread נפרד וכותב
progress דרך signals. ה-GUI מציג מה שהוא מקבל. בסיום, ה-worker emit-ים
status sukko (records_pulled / errors).

הפרדה בין UI ל-pipeline: nightly_sync.run_full() נשאר זהה — הוא ה-truth.
ה-worker רק עוטף אותו.
"""
from __future__ import annotations
from qtpy.QtCore import QThread, Signal as pyqtSignal
from logger import logger


class SyncWorker(QThread):
    """מריץ nightly_sync.run_full ב-background.

    Signals:
      step_started(str)  — תחילת שלב (priority_rolling / partbal / iaa)
      step_done(str, dict) — סיום שלב עם הסיכום שלו
      finished_ok(dict)  — סנכרון הסתיים בהצלחה, dict = records_pulled
      finished_failed(str)  — נכשל לחלוטין
      finished_partial(dict, str)  — חלקית: גם תוצאות וגם שגיאה
    """
    step_started     = pyqtSignal(str)
    step_done        = pyqtSignal(str, dict)
    finished_ok      = pyqtSignal(dict)
    finished_failed  = pyqtSignal(str)
    finished_partial = pyqtSignal(dict, str)

    def __init__(self, days: int = 30, skip_iaa: bool = False,
                 triggered_by: str = 'app-startup'):
        super().__init__()
        self.days = days
        self.skip_iaa = skip_iaa
        self.triggered_by = triggered_by
        self._pulled: dict = {}
        self._errors: list[str] = []

    def run(self):
        try:
            # ה-import lazy כדי לא לטעון את הקוד הזה כשה-app רק עולה
            from nightly_sync import (
                sync_priority_rolling, sync_partbal, sync_iaa
            )
            from sync_runs import start_run, update_progress, finish_run

            run_id = start_run(triggered_by=self.triggered_by)
            logger.info("SyncWorker started run %d", run_id)

            self._step(run_id, 'priority_rolling', sync_priority_rolling,
                       days=self.days)
            self._step(run_id, 'partbal', sync_partbal)
            if not self.skip_iaa:
                self._step(run_id, 'iaa', sync_iaa)

            if not self._errors:
                status = 'ok'
            elif self._pulled:
                status = 'partial'
            else:
                status = 'failed'

            finish_run(
                run_id=run_id,
                status=status,
                records_pulled=self._pulled,
                errors_count=len(self._errors),
                last_error_text='\n'.join(self._errors) if self._errors else None,
            )

            if status == 'ok':
                self.finished_ok.emit(self._pulled)
            elif status == 'partial':
                self.finished_partial.emit(self._pulled, '\n'.join(self._errors))
            else:
                self.finished_failed.emit('\n'.join(self._errors))

        except Exception as e:
            logger.exception("SyncWorker crashed")
            self.finished_failed.emit(f"{type(e).__name__}: {e}")

    def _step(self, run_id: int, name: str, fn, **kwargs):
        """עוטף שלב יחיד עם signals + try/except."""
        from sync_runs import update_progress
        self.step_started.emit(name)
        try:
            # ה-pipeline functions מקבלים logger; נספק אחד דמה כדי שלא יקרסו
            result = fn(lg=logger, **kwargs)
            self._pulled.update(result)
            update_progress(run_id, self._pulled)
            self.step_done.emit(name, result)
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            logger.error("SyncWorker step %s failed:\n%s", name, tb)
            self._errors.append(f"{name}: {type(e).__name__}: {e}")
            # ממשיכים לשלב הבא — אל תכשיל את כל הסנכרון בגלל שלב יחיד
