# MemsiInterface — תוכנית מעבר ל-Production

מסמך עבודה. סדר הספרינטים מבוסס על תלויות-תוכן: כל ספרינט מניח שהקודמים בוצעו. ההערכות בזמן הן עבור פיתוח-יחיד שעובד עליה כעיסוק צדדי.

ההיגיון בחלוקה: שלוש שכבות. **A — יציבות**, מטפלת בכל מה שיש היום ועושה אותו ראוי לרצים מקבילים. **B — שינוי זרימה**, משנה את הארכיטקטורה כך שה-GUI הוא viewer ולא fetcher, ומכניסה את שני מקורות הנתונים שחסרים (BOM, ספירות). **C — שדרוגים**, משפרת את התחזיות ויוצרת את הכלי המאוחד למנהל-מלאי.

יציבות לפני תוספות,
---

## Track A — יציבות

### A1: תשתית DB וניקוי קוד-מת

**מטרה.** להעביר את כל הקוד למסלול חיבור אחד עם pool, לסגור את שכבת ה-shim הכפולה של pricing/branches/warehouses, ולהיפטר מהכפילות בין `db_setup.py` ל-`migrations/`.

**מה נכלל.**
- כתיבת `db_config.py` חדש עם `ThreadedConnectionPool`, `get_conn()` כ-context manager שמטפל ב-commit/rollback אוטומטית, ו-`DATABASE_URL` כקלט עיקרי (עם fallback להרכבה מ-DB_HOST/PORT/NAME/USER/PASSWORD לתאימות).
- עדכון `db_config.example.py` כך שיהיה ניתן להעתיק ולקבל קובץ עובד מיד.
- מעבר של כל הקבצים שעדיין משתמשים ב-`psycopg2.connect(**DB_CONFIG)` ל-`get_conn()`: `cache_manager.py`, `inventory_manager.py`, `forecast_db.py`, `db_setup.py`, `migrate.py`.
- העברת כל ה-DDL מ-`db_setup.py` למיגרציה חדשה `004_cache_tables.sql` תחת `migrations/`. מחיקת `db_setup.py`.
- שינוי `gui_app.py` ש-`_run_health_checks` לא ייצור טבלאות, רק יבדוק חיבור.
- שינוי כל ה-`TIMESTAMP` ב-schema ל-`TIMESTAMPTZ` במיגרציה חדשה (`005_timestamps_tz.sql`).
- מחיקת dead code: ה-block של ה-prompt-injection ב-`inventory_analysis.py` שורה 75 והלאה, התלויות `sqlalchemy` ו-`scikit-learn` מ-requirements.txt, hidden-import של `sklearn` ב-spec, ה-tier-ים `QAS` ו-`LAUFER` הבודדים מ-`pricing_data.py`.
- ניקוי בדיקת ה-import הכפול של `QTabWidget` ב-`gui_app.py` שורה 16-19.
- מחיקת קבצי ה-shim ההיסטוריים (`pricing_data.py`, `branch_names.py`, `warehouse_config.py`, `product_identification.py`) **לאחר שמוודאים** ש-`migrations/002_seed_from_code.py` לא נדרשת יותר (היא רצה פעם אחת בעבר).
- עדכון `TARGET_CUSTOMERS` ב-`fetch_combined.py` כך שיישלף מ-`customers` table במקום להיות hardcoded.

**מה לא נכלל.** שינויים לוגיים בשום פיצ'ר. רק ניקיון. אם משהו נראה כמו refactor של אלגוריתם — דחוף לספרינט אחר.

**Definition of Done.** המערכת רצה כרגיל, כל הבדיקות הידניות עוברות. `grep -rn "psycopg2.connect"` מחזיר רק את `db_config.py`. כל הטבלאות נוצרות דרך `migrate.py` בלבד. clone נקי + `pip install -r requirements.txt` + `cp db_config.example.py db_config.py` + `python migrate.py` + `python gui_app.py` עובד מאפס.

**הערכת זמן.** 5–7 ימי עבודה.

**תלויות.** אין. זה הספרינט הראשון.

---

### A2: Exception handling יסודי

**מטרה.** להחליף את ה-pattern הנוכחי של "תפוס Exception רחב, הצג ב-MessageBox, סיים" ב-pattern עקבי שמבדיל בין שלוש קטגוריות שגיאה: **קונפיגורציה שבורה** (fail-fast ב-startup), **תקלת רשת/DB זמנית** (retry או הצגה למשתמש עם אופציית-נסה-שוב), **באג בקוד** (log מלא, הצגה מצומצמת למשתמש, אבל לא קריסה).

**מה נכלל.**
- כתיבת קונבנציית-שגיאות בקובץ `errors.py` חדש: `ConfigError`, `TransientNetworkError`, `BusinessRuleError`. כל אחד עם base class משלו ושיטה `to_user_message()` שמחזירה טקסט בעברית מתאים.
- מעבר על `fetch_combined.py:18` ו-`inventory_manager.py:16` (ה-`os.environ['PRIORITY_AUTH_HEADER']`) לקריאה lazy שזורקת `ConfigError` במידע ברור אם המשתנה חסר.
- הרחבת `_RETRY` ב-`fetch_combined` כך שיתפוס גם 5xx (לא רק `ConnectionError`/`Timeout`). הוספת `_RETRY` דומה ב-`inventory_manager.py` עבור PARTBAL.
- שימוש ב-`try/finally` בכל מקום שיש `psycopg2.connect` ישיר — כך שגם אם פעולה נכשלת באמצע, החיבור נסגר. (בעיקר רלוונטי בקבצים שעוד לא עברו ל-pool, אם נשארו כאלה אחרי A1.)
- תיקון `forecast_db.ForecastDB.__exit__` שיעשה rollback אם יש exception, commit אם אין.
- העברת כל ה-workers ל-pattern עקבי: ב-`logger.exception` בכל workers (כיום רק חצי משתמשים ב-זה). יש כיום ברוב ה-tabs רק `import traceback; self.error.emit(traceback.format_exc())` בלי log — צריך להוסיף `logger.exception(...)` לכולם.
- שינוי ב-`gui_app.py:_run_health_checks`: אם DB נכשל, להציג banner קבוע ב-status-bar במקום QMessageBox קופץ (שכרגע חוסם את כל ה-app). ה-tabs צריכים לבדוק זמינות-DB בכל פעולה ולהתנהג בחן אם אין.
- הוספת `closeEvent` ל-`MainWindow` שמחכה ל-workers לסיים (`worker.wait(timeout=5000)`) לפני שסוגר. אחרת QThread נהרג באמצע ועלול להשאיר connection פתוח.
- העברה של `BaseTabWorker` כברירת-מחדל. כפי שדנו: workers מורכבים נשארים classes (`ForecastWorker`, `BranchSnapshotWorker`, `ProcurementWorker`), השאר עוברים ל-`run_in_worker` עם closure: `BranchLoadWorker`, `BranchReportWorker`, `MultiMonthReportWorker`, `InventoryWorker`, `InventoryAnalysisWorker`, `HealthCheckWorker`.
- תיקון ה-bug של stdout-redirect ב-`ForecastWorker.run` ו-`BranchSnapshotWorker.run`: להעיף את ה-`sys.stdout = _Emit(...)` ולהחליף ב-`logger.info` בתוך `forecast_engine.run_all_models`. ה-progress-string יעבור דרך ה-signals של ה-worker כפרמטר לפונקציה.

**מה לא נכלל.** עיצוב מחדש של ה-UI לטיפול בשגיאות (toast, retry buttons וכו'). זה רק ה-pipe הפנימי.

**Definition of Done.** מותר להפעיל את האפליקציה בלי `.env`, או עם DB לא נגיש, או עם פריוריטי לא נגיש — כל תרחיש מציג מסר ברור ולא קורס. `grep -rn "except Exception" --include="*.py"` נותן רק exceptions שיש להם logger.exception או טיפול ייעודי. `grep -rn "sys.stdout =" --include="*.py"` מחזיר 0.

**הערכת זמן.** 4–6 ימי עבודה.

**תלויות.** A1 (כי הוא משנה את שכבת ה-DB שעליה נבנה ה-handling).

---

### A3: ניהול קאש וזיכרון

**מטרה.** לוודא שמתחת לעומס של 1–2 משתמשים מקבילים, אין זליגות זיכרון, אין כפילויות-קריאה, ופעולות קריטיות (fetch חודשי, save_run) מסתיימות בזמנים סבירים.

**מה נכלל.**
- **Bulk inserts.** החלפת כל ה-INSERT-loops ב-`psycopg2.extras.execute_values`:
  - `cache_manager.save_documents` ו-`save_logfile` (זה השדרוג עם ה-impact הגדול ביותר — fetch חודשי ירד ב-80%).
  - `forecast_evaluation.save_run` ב-loop של predictions.
  - `migrations/002_seed_from_code.py` — כבר רץ, אבל אם יסבו אותה למיגרציה אחרת בעתיד.
  - `forecast_db.bulk_upsert_history` (כיום רץ row-by-row למרות השם).
- **Vectorize של `combine_data`.** העברת ה-`identify_luggage` מ-`apply(axis=1)` ל-קריאה אחת על תיאורים-ייחודיים פלוס `df['col'].map(lookup_dict)`. בדיוק אותה תפוקה, אבל פי 10–20 מהר. אותו דבר ל-`is_repair_item`/`get_repair_price`.
- **invalidation בקאש של `domain_repository`.** הוספת TTL של 5 דקות לכל קריאת `_cached`. במקום לשמור לעולם בזיכרון, לרענן מ-DB אחרי 5 דקות. בקונפיגורציה בודדת זה זול. בקונפיגורציה של 2 משתמשים זה פותר את בעיית העדכון של User A שלא מגיע ל-User B במהירות (במקום אף פעם → 5 דקות).
- **`ForecastDB` lifecycle.** במקום שהוא יחזיק חיבור פתוח לכל החיים של ה-app, להמיר אותו לשימוש ב-`get_conn()` בכל קריאה. החיבור נשאר ב-pool שמנהל את הניצול נכון.
- **`CacheManager` lifecycle.** מעבר ל-`with` block בכל מקום שמשתמשים בו. ה-class עצמו צריך להפוך ל-context manager (`__enter__`/`__exit__`) שמשחרר connection ל-pool.
- **`InventoryTab` rendering.** העברה מ-`QTableWidget` ל-`QTableView` עם `QAbstractTableModel` מותאם — או לפחות עטיפת ה-fill loop ב-`setUpdatesEnabled(False)/(True)` כדי למנוע redraw בכל תא. מודל מלא הוא הפתרון הנכון אבל דורש יותר עבודה; עטיפה היא 5 שורות שיתנו רוב התועלת.
- **OData `$select` projection.** הוספת `$select` ל-`fetch_documents` ו-`fetch_logfile` עם רק העמודות שבאמת בשימוש. זה לא קוד-יותר-נקי, זה bandwidth-נמוך-יותר.

**מה לא נכלל.** מעבר ל-NOTIFY/LISTEN לסנכרון cache בין processes. ב-1–2 משתמשים זה overkill; TTL של 5 דקות מספיק.

**Definition of Done.** fetch של חודש שלם (~50K שורות logfile) מסתיים תוך 30 שניות (כיום ~3 דקות). `combine_data` של 100K שורות מסתיים תוך 5 שניות (כיום ~30 שניות). אחרי 8 שעות-app חי, השימוש בזיכרון לא צמח מעבר ל-200MB. עדכון מחיר על מכונה אחת נראה במכונה השנייה תוך 5 דקות.

**הערכת זמן.** 4–5 ימי עבודה.

**תלויות.** A1 (pool), A2 (try/finally בכל מקום).

---

### A4: Failover מקומי לקריאה

**מטרה.** אם ה-DB לא זמין, האפליקציה ממשיכה לעבוד ב-read-only מ-cache מקומי. כתיבות מושבתות, banner ברור.

**מה נכלל.**
- יצירת `~/.memsi/local_cache.db` כ-SQLite. עליו רפליקה של ה-domain tables: `customers`, `pricing_tiers`, `customer_repair_prices`, `customer_replacement_prices`, `supplier_repair_prices`, `supplier_replacement_prices`, `branches`, `warehouses`, `luggage_identification`, `forecast_events`. גם `forecast_history` (ב-rolling window של ~12 חודשים מספיק).
- מודול `local_mirror.py` עם פונקציה `sync_from_postgres()` שעושה delta-sync לפי `updated_at` — בכל חיבור מוצלח ל-postgres, ה-rows שהשתנו מאז ה-sync האחרון מועתקים ל-SQLite.
- שכבת abstraction ב-`domain_repository`: כל פונקציית read מקבלת flag `_OFFLINE_MODE` שכשהוא דלוק, היא קוראת מ-SQLite במקום מ-postgres. כל פונקציית write בודקת את ה-flag וזורקת `BusinessRuleError("מצב לא-מקוון, לא ניתן לערוך")`.
- ה-`HealthCheckWorker` (אחרי A2 הוא `run_in_worker`) מעדכן את ה-flag בפעם ש-postgres חוזר/נופל.
- Banner גלוי ב-status-bar של `MainWindow`: כשה-flag דלוק → "מצב לא-מקוון. נתונים נכון ל-{timestamp}" ברקע צהוב.
- ה-tabs של עדכונים (`UpdatesTab`, `UnidentifiedProductsTab` בפעולת import) מסתירים את כפתורי ה-save או מציגים אותם כ-disabled עם tooltip מסביר.
- בדיקה ידנית של תרחיש: לעצור postgres, לפתוח את האפליקציה, להפיק דוח, להשיב את postgres, לוודא שה-flag חוזר.

**מה לא נכלל.** sync דו-כיווני. SQLite הוא read-only mirror. כל write הולך ישר ל-postgres, אין offline-write.

**Definition of Done.** המערכת מתפקדת לאורך נפילה של postgres של עד 24 שעות. כל ה-read-tabs עובדים. כל ה-write-tabs מציגים מסר ברור. אחרי שובו של postgres, ה-sync מתעדכן אוטומטית בפעולה הבאה של המשתמש.

**הערכת זמן.** 5–6 ימי עבודה.

**תלויות.** A1, A3.

---

## Track B — שינוי זרימת נתונים

### B1: ריצה לילית אוטומטית

**מטרה.** להעביר את ה-fetch של DOCUMENTS_D + LOGFILE + PARTBAL מ-event-driven ב-GUI ל-scheduled-job ברקע. ה-GUI עובר להיות viewer של נתוני-cache בלבד.

**מה נכלל.**
- סקריפט חדש `nightly_sync.py` שמשמש כ-entry-point ל-Windows Task Scheduler / cron.
- אסטרטגיית-משיכה rolling-window: בכל ריצה, הסקריפט מושך מחדש את 30 הימים האחרונים של documents/logfile (מטפל ב-retroactive edits) ועושה refresh של PARTBAL.
- Logging ייעודי לסקריפט הלילי ב-`~/.memsi/logs/nightly_*.log` (נפרד מהקובץ של ה-GUI כי הם רצים בנפרד).
- exception handling מקיף בסקריפט: אם פריוריטי לא זמין, retries אקספוננציאליים עד 4 שעות. אם DB לא זמין, מחכה ומנסה שוב. אם משהו עוד לא נפתר אחרי שעה — שולח alert (פשוט: כותב marker file ש-ה-GUI מציג כשמתחבר).
- חישוב טבלת summary ב-DB: `sync_runs` עם started_at, finished_at, records_pulled, errors_count.
- ב-GUI: הסרת כפתור "ריענון" מ-`ReportGeneratorTab` (או הפיכתו ל-"שלח-בקשת-רענון-דחופה" שכותב flag ל-DB וה-job-הלילי מטפל). הוספת label ב-status-bar שמראה "נתונים נכון ל-{תאריך-ה-sync-האחרון}".
- מסמך README קצר על installation: `schtasks /create ...` (Windows) או `crontab -e` (Linux).

**מה לא נכלל.** הפיכת ה-job ל-service רץ-תמיד. זה לילי בלבד; אם נדרש sync תוך-יום, המשתמש יכול להריץ את `nightly_sync.py` ידנית מהטרמינל או מ-shortcut.

**Definition of Done.** סקריפט רץ לבד אוטומטית כל לילה ב-23:00. מתעד את הריצה ב-DB ובלוג. אם אתה הופך את פריוריטי כבוי לפני הריצה, הסקריפט מנסה כל 15 דקות עד 04:00. ה-GUI מציג את התאריך של ה-sync האחרון. כפתור "ריענון" לא קיים יותר ב-`ReportGeneratorTab`.

**הערכת זמן.** 4–5 ימי עבודה.

**תלויות.** A1, A2, A3 — צריך תשתית-שגיאות יציבה לפני שמוציאים job בלתי-מבוקר.

---

### B2: סטים ופירוקם האוטומטי

**מטרה.** להוסיף תמונת-מלאי שמייצגת את האמת — כשסט מגיע לסניף, המערכת רואה אותו כמרכיביו ולא כיחידה אחת.

**מה נכלל.**
- **שלב מחקר (לפני קוד):** בירור באמצעות אנשי ה-IT/הפריוריטי איזו טבלה ב-OData מחזיקה את מבנה הסטים. החשודות העיקריות הן `PARTREC` או `PARTARC` (רכיבי הרכבה) או `MAKEAC` (BOM). יש לבדוק עם דוגמה ידנית: לקחת מק"ט-של-סט קיים ולנסות endpoint-ים שונים בדפדפן עם credentials, לראות איפה מופיע פירוק לרכיבים.
- ברגע שיודעים — fetch אחת לשבוע (זה נתון יציב) של ה-BOM ל-table חדשה `kit_components(parent_sku, child_sku, child_quantity)`.
- טבלה חדשה `local_inventory(warehouse_code, sku, quantity, last_movement_at)` ב-postgres — זו תהיה תמונת-המלאי "האמיתית" שלנו, נפרדת מ-PARTBAL.
- שכבת חישוב: בכל ריצה לילית, אחרי שמושכים את LOGFILE החדש, פוגעים בלוגיקה הבאה לכל תנועת IN לסניף עם partname שיש לו שורות ב-`kit_components`:
  - מחסירים 1 מהסט במלאי-המקומי (למקרה שהסט עצמו הגיע "כאמיתי")
  - מוסיפים child_quantity מכל child_sku
  - תנועת OUT (מכירה) של רכיב בודד מתבצעת רגילה — ירידה במלאי הרכיב
- ב-GUI: לשונית "מעקב מלאי" הקיימת (`InventoryTab`) מקבלת toggle "תצוגה מאוחדת (אמיתית)" / "תצוגת פריוריטי גולמית". ה-toggle מחליף בין PARTBAL ל-`local_inventory`.
- audit log: כל split-של-סט נרשם ב-table חדש `kit_split_log` עם date/warehouse/parent_sku/components_added — ל-debugging ולפיוס מול הנהלת חשבונות אם פעם יידרש.

**מה לא נכלל.** טיפול בסטים מורכבים יותר (סט-של-סטים, או רכיבים אופציונליים שמופיעים-לא-מופיעים). ההנחה היא BOM פשוט שטוח. אם הופעות מורכבות יותר תתגלינה במחקר, נעצור ונתכנן מחדש.

**Definition of Done.** סט שנשלח לסניף נראה ב-"תצוגה מאוחדת" כרכיביו. ספירה ידנית בסניף מסכימה (או קרובה) למה ש-`local_inventory` מראה.

**הערכת זמן.** 6–8 ימי עבודה. **שלב המחקר עלול לקחת זמן רב יותר אם אנשי הפריוריטי לא זמינים — צריך לתאם מראש.**

**תלויות.** B1 (ה-pipe הלילי הוא המקום שבו זה רץ).

---

### B3: ספירות מלאי כ-anchor

**מטרה.** לעגן את חישוב המלאי-האמיתי לנקודות-ייחוס תקופתיות, כך שה-drift לא מצטבר.

**מה נכלל.**
- בירור ראשון (כפי שכתבתי בצ'אט): האם ל-`LASTCOUNT` יש עמודת תאריך ישירה, או שצריך לעשות JOIN עם `DOCUMENTS_D` דרך DOCNO. אם השני — צריך לדעת מראש לעשות שני fetches בריצה.
- הוספת `LASTCOUNT` כ-OData endpoint שלישי ב-`fetch_combined.py` (או חדש, `fetch_stock_counts.py`). משיכתו ב-job הלילי.
- table חדשה `stock_counts(warehouse_code, sku, count_date, counted_quantity, source_docno)`. שמורה היסטורית (לא רק האחרונה — ייתכן שנרצה לראות trend).
- שינוי בלוגיקת `local_inventory`:
  - בכל פעם שיש ספירה חדשה ל-`(warehouse, sku)` — מאפסים את ה-quantity ב-`local_inventory` ל-`counted_quantity`, ושומרים `last_count_at = count_date`.
  - לאחר מכן, רק תנועות עם `curdate > last_count_at` מצטברות.
- חישוב delta-from-priority: כל פעם שגם PARTBAL וגם ה-`local_inventory` המאוחד שלנו זמינים, לחשב `priority_quantity - reconciled_quantity`. זה ה-drift. אם הוא קטן (פחות מ-3 יחידות) — זה רעש. אם גדול — חוסר עקביות שצריך לבחון. UI: קולומה צבועה אדום ב-`InventoryTab` כשה-delta גבוה.
- KPI חדש בלוח-הבקרה הליליי: "ממוצע drift אחרון", "מספר פריטים עם drift מעל סף". למוניטור על-זמן.

**מה לא נכלל.** UI להזנת ספירות ידנית. ההנחה היא שספירות נכנסות לפריוריטי ולא לפלטפורמה הזו ישירות. אם תתברר תרחיש שבו זה לא המצב, ניתוסף ספרינט.

**Definition of Done.** בכל סניף שאי-פעם נספר, יש "נקודת אפס" שממנה החישוב המקומי מתחיל. ה-drift הממוצע מול PARTBAL הוא תחת 5%. אם יש פריט עם drift מעל 20% הוא מסומן באופן ויזואלי.

**הערכת זמן.** 3–4 ימי עבודה (יחסית קצר כי המבנה בנוי על מה ש-B2 כבר הקים).

**תלויות.** B2 (`local_inventory` כבר קיימת).

---

## Track C — שדרוגים

### C1: תיקוני נכונות בתחזיות

**מטרה.** לתקן את הבאגים שזיהינו בסקירה הראשונה, לפני שמוסיפים פיצ'רים חדשים.

**מה נכלל.**
- **תיקון `_extend_events` ב-backtest.** לבדוק עבור כל future row האם year_month שלו כבר קיים ב-events_df הקלטי, ובמקרה כזה לא לדרוס. זה תיקון-של-2-שורות אבל הוא משנה לחלוטין את אמינות ה-MAE/RMSE/MAPE שמוצגים. כל ריצות-העבר ב-`forecast_runs` מהמערכת הנוכחית הן עכשיו פגועות-נתונים — או למחוק אותן או להריץ מחדש כדי לקבל metrics נקיים.
- **`MODEL_VERSION`** קבוע ב-`forecast_engine.py` שנכלל ב-hash של `forecast_cache._key`. כל שינוי בפרמטרי-מודל = bump של הגרסה = cache invalidation אוטומטי.
- **הסרת multicollinearity ב-Prophet:** הורדת `is_routine` מרשימת ה-regressors (הוא בדיוק 1 פחות סכום השלושה האחרים). ב-XGBoost לא קריטי, אבל לעקביות אפשר גם שם.
- **פישוט ה-feature set ב-XGBoost:** הסרה של אחד מתוך {`season`, `is_summer_peak`, `sin_month`/`cos_month`} — שלושתם מקודדים את אותו דבר. הצעה: השארת `sin_month`/`cos_month` (הם רציפים, מה ש-XGBoost מטפל בהם הכי טוב) והסרת השאר.
- **הוספת אופק 9-חודשים ו-טווח-מותאם ל-`horizon_combo`.** עבור הטווח-מותאם, החלטת UX: שני QDateEdit ושימוש ב-relativedelta. לבחור על המקום שהטווח חייב להתחיל מהחודש-הבא (פשוט יותר), או לאפשר טווח עתידי-עתידי (מורכב יותר אבל יותר גמיש לתכנון לטווח-ארוך). דעתי: להתחיל פשוט, ולהוסיף את הגמישות אם תידרש.
- **תיקון `forecast_xgboost` עם residual_std:** במקום לחשב residual std פעם אחת על כל ה-train, לחשב roll std על residuals של 12 חודשים אחרונים (יותר רגיש לרמת-רעש עכשווית).

**מה לא נכלל.** שינוי מבני בארכיטקטורת המודלים (זה בספרינט הבא).

**Definition of Done.** ריצה של backtest על אותם נתונים נותנת מספרים בעלי אמינות מוכחת (אפשר לעשות sanity-check ידני: לקחת חצי-שנה היסטורית, להריץ-מ-N חודשים-אחורה, ולראות שה-forecast קרוב לאמת לפי MAE).

**הערכת זמן.** 3–4 ימי עבודה.

**תלויות.** אחרי כל ה-Track A. רצוי גם אחרי B1, אבל לא חובה.

---

### C2: תחזיות per-cell עם reconciliation

**מטרה.** המעבר מתחזית-אחת-על-אגרגציה לתחזית פר-(branch × dimension-cell), עם reconciliation למעלה.

**מה נכלל.**
- שינוי ב-`forecast_engine.py`: פונקציית-עטיפה חדשה `forecast_hierarchical(hist_df, horizon, events_df, context, level='branch_cell')` שמחלקת את ה-hist_df ל-(branch, cell) ומריצה מודלים על כל אחד.
- אם רמת-העלים פגיעה (cell עם 5 חודשים נתונים בלבד), נופלים אוטומטית למודל-משותף לאותה דרגה/גודל בכל הסניפים. אם גם זה דליל, נופלים לממוצע-קטגוריה ביחד עם trend גלובלי. שלב-נפילה (graceful degradation) קריטי.
- reconciliation: bottom-up פשוט (סכמת התחזיות פר-cell = תחזית האגרגט). אם המאזן נמצא לא-טוב יותר ממודל-ישיר על האגרגט, אופציה לעבור ל-MinT או OLS reconciliation. בשלב ראשון, bottom-up פשוט מספיק.
- `forecast_metrics` מורחב: per-(model, branch, cell), לא רק per-model. כך אפשר להראות "ARIMA טוב לסניף 308 בקטגוריית 'גדולה מותג קשיחה' אבל גרוע ל-טרולי".
- **בחירת champion אוטומטית.** לכל (branch, cell), המודל עם MAE הנמוך ביותר הופך לברירת-מחדל. ה-UI מציג רק את ה-champion וה-actuals, עם אופציה למשתמש לראות את ה-3 ביחד.
- שינויי UI: לשונית "תחזית" מציגה כברירת-מחדל את ה-champion forecast לכל cell בהיררכיה. הלשונית "תחזית כוללת" עוברת ל-tab משני. המידע "AC = 89% בנתונים האחרונים" מוצג ליד הצפי.

**מה לא נכלל.** ensemble-weighted-by-accuracy. champion-בלבד מספיק כפתרון ראשוני; אם תמיד שני מודלים קרובים מאוד, נחשוב על ensemble.

**Definition of Done.** עבור slice טיפוסי (5 סניפים × 24 cells = 120 צירופים), המערכת מייצרת 120 תחזיות ב-זמן סביר (פחות מ-2 דקות). ה-UI מציג אותן בצורה ניתנת-לעיכול. הצוות יכול להגיד "המלצת רכש לחודש הבא היא 30 'בינונית מותג רכה'", לא רק "המלצת רכש לחודש הבא היא 250 יחידות סך-הכל".

**הערכת זמן.** 8–10 ימי עבודה. זה הספרינט הכי משמעותי בכל התוכנית.

**תלויות.** C1.

---

### C3: כלי תכנון מאוחד

**מטרה.** הצגת מסך-בעל-משמעות-אופרטיבית למנהל-מלאי / רוכש: כל המידע שהוא צריך כדי להחליט "הזמן X יחידות מקטגוריה Y לסניף Z" — בלי לקפוץ בין lashוניות.

**מה נכלל.**
- לשונית חדשה `PlanningTab` (או הרחבה משמעותית של "תמונת מצב סניף").
- בחירת סניף + תאריך-יעד.
- טבלה אחת לכל cell (size × grade × material): מלאי-נוכחי-בסניף (מ-`local_inventory` המאוחד), מלאי-במרלו"ג (מאותה טבלה למחסן-המרלו"ג), צפי-מכירות (מ-C2 — ה-champion forecast לאותו cell), המלצת-הזמנה.
- לוגיקת המלצה: `recommended = max(0, expected_sales × (1 + safety_factor) - current_stock)`. safety_factor מתבסס על Newsvendor אבל פר-cell ולא על האגרגט (זה השדרוג מ-c2).
- כפתור "הפק טיוטת הזמנה" שמייצר אקסל עם הכמויות, מוכן לחתימה ושליחה למרלו"ג.
- היסטוריה: כפתור "ריצות קודמות" שמראה את ההמלצות שהוצעו לחודשים האחרונים — כדי שאפשר לראות איך ההמלצה השתנתה ולהבין למה.
- אופציה רחבה יותר: למנהל-הרכש שעובד על יבוא, מסך דומה אבל ברמת-החברה (לא ברמת-סניף) עם ה-9 חודשים שדיברנו עליהם כ-default-אופק.

**מה לא נכלל.** integration אוטומטי עם פריוריטי לשליחת הזמנה אוטומטית. הטיוטה היא Excel, השליחה ידנית. אם בעתיד יידרש — אפשר להוסיף.

**Definition of Done.** מנהל-מלאי יכול בלחיצה אחת לראות מה הסטטוס המלא של סניף ולהזמין בו. הזמן מ-"רוצה להזמין" ל-"שלחתי טיוטה למרלו"ג" יורד מ-30 דקות (חישוב ידני) ל-2 דקות.

**הערכת זמן.** 5–6 ימי עבודה.

**תלויות.** C2 + B2 + B3 (כי הוא משתמש בכל הזרמי-הנתונים החדשים).

---

## סיכום זמן

- **Track A:** 18–24 ימי עבודה (~4–6 שבועות בקצב סביר)
- **Track B:** 13–17 ימי עבודה (~3–4 שבועות)
- **Track C:** 16–20 ימי עבודה (~4–5 שבועות)
- **סה"כ:** 47–61 ימי עבודה, או 11–15 שבועות אם זה עיסוק מלא, יותר אם בערבים בלבד.

זה מציאותי לתת-שנה של עבודה לסירוגין. כל ספרינט הוא חתיכה שאפשר לעצור אחריה ולנשום, ולהעבירה לשימוש בלי להמתין לשאר. אחרי A4 כבר יש לך מערכת יציבה שמתאימה ל-2 משתמשים. אחרי B3 יש מודל-מלאי אמיתי. אחרי C3 יש כלי-תכנון מלא.

שאלות שכדאי להחזיק בראש בזמן הביצוע:

1. **איפה מתאמת בדיקה ידנית של תרחישי-end-to-end?** אין test suite בפרויקט. עבור פרודקשן זה בסדר במידה, אבל אחרי A4 מומלץ לפחות כתב-בדיקה manualי ל-5 התרחישים החשובים שאפשר להריץ אחרי כל ספרינט.
2. **גרסאות.** אחרי A1, מומלץ לתייג את ה-repo (`v0.1.0`). אחרי כל ספרינט, גרסה חדשה. כך אם משהו נשבר בעדכון, יש למה לחזור.
3. **רוצה לעצור באמצע ולחשוב מחדש?** סדר הספרינטים אינו מקודש. אם אחרי A2 תרגיש שעדיף לקפוץ ל-B1 לפני להמשיך A3 — זה לגיטימי. רק B2/B3/C2/C3 הם שצריכים תלוּת קודמת ברורה.

