import requests
import pandas as pd
import calendar
from datetime import datetime
from dateutil.relativedelta import relativedelta
from pricing_data import get_repair_price, is_repair_item, get_replacement_price
from product_identification import identify_luggage
from cache_manager import CacheManager

AUTH_HEADER = "Basic QVBJUjowMDAx"
DOCUMENTS_URL = "https://priority.newcinema.co.il/odata/Priority/tabula.ini/ncinema/DOCUMENTS_D"
LOGFILE_URL = "https://priority.newcinema.co.il/odata/Priority/tabula.ini/ncinema/LOGFILE"

TARGET_CUSTOMERS = [
    '360010009', '360010035', '360250034', '360250041', '360040004',
    '360050004', '360250026', '360200017', '360250040', '360250014',
    '360250027', '360250029', '360250028', '360250031', '360250032',
    '360250030', '360250038', '360250039', '360190002', '360250033'
]

def fetch_documents(start_date, end_date):
    headers = {"Authorization": AUTH_HEADER}
    customer_filter = ' or '.join([f"CUSTNAME eq '{c}'" for c in TARGET_CUSTOMERS])
    params = {
        '$filter': f"(CURDATE ge {start_date}T00:00:00Z and CURDATE le {end_date}T23:59:59Z) and ({customer_filter})"
    }
    
    response = requests.get(DOCUMENTS_URL, headers=headers, params=params)
    if response.status_code != 200:
        print(f"Error fetching documents: {response.text}")
        return []
    
    data = response.json()
    return data.get('value', [])

def fetch_logfile(start_date, end_date):
    headers = {"Authorization": AUTH_HEADER}
    customer_filter = ' or '.join([f"CUSTNAME eq '{c}'" for c in TARGET_CUSTOMERS])
    params = {
        '$filter': f"(CURDATE ge {start_date}T00:00:00Z and CURDATE le {end_date}T23:59:59Z) and ({customer_filter})"
    }
    
    response = requests.get(LOGFILE_URL, headers=headers, params=params)
    if response.status_code != 200:
        print(f"Error fetching logfile: {response.text}")
        return []
    
    data = response.json()
    return data.get('value', [])

def fetch_with_cache(start_date, end_date):
    """
    משיך נתונים עם שימוש ב-cache
    אם הנתונים קיימים ב-cache - מחזיר משם
    אחרת - מושך מ-API ושומר ב-cache
    """
    cache = CacheManager()
    
    # בדיקה אילו חודשים חסרים
    missing_docs = cache.get_missing_months(start_date, end_date, 'documents')
    missing_logs = cache.get_missing_months(start_date, end_date, 'logfile')
    
    # משיכת חודשים חסרים
    for year_month in set(missing_docs + missing_logs):
        year, month = map(int, year_month.split('-'))
        last_day = calendar.monthrange(year, month)[1]
        month_start = f"{year}-{month:02d}-01"
        month_end = f"{year}-{month:02d}-{last_day}"
        
        print(f"  Fetching from API: {year_month}")
        
        if year_month in missing_docs:
            docs = fetch_documents(month_start, month_end)
            cache.save_documents(docs, year_month)
            cache.update_metadata('documents', year_month, month_start, month_end, len(docs))
        
        if year_month in missing_logs:
            logs = fetch_logfile(month_start, month_end)
            cache.save_logfile(logs, year_month)
            cache.update_metadata('logfile', year_month, month_start, month_end, len(logs))
    
    # שליפה מ-cache
    print(f"  Loading from cache: {start_date} to {end_date}")
    documents = cache.get_documents(start_date, end_date)
    logfile = cache.get_logfile(start_date, end_date)
    
    cache.close()
    return documents, logfile

def combine_data(documents, logfile_records):
    # המרה ל-DataFrame
    docs_df = pd.DataFrame([{
        'תעודה': d.get('DOCNO'),
        'תאריך': d.get('CURDATE'),
        'הערה 1 לכתיבה': d.get('RETL_DETAILS1'),
        'מספר לקוח': d.get('CUSTNAME'),
        'שם לקוח': d.get('CUSTDES'),
        'שם לקוח קופה': d.get('CDES'),
        'פרטים': d.get('DETAILS'),
        'סטטוס': d.get('STATDES'),
        'לטיפול': d.get('OWNERLOGIN'),
        'סניף': d.get('BRANCHNAME')
    } for d in documents])
    
    log_df = pd.DataFrame([{
        'תעודה': l.get('LOGDOCNO'),
        'מקט': l.get('PARTNAME'),
        'תיאור מוצר': l.get('TOPARTDES'),
        'כמות': l.get('TQUANT'),
        'מחיר ליחידה': l.get('UCOST'),
        'מספר לקוח_log': l.get('CUSTNAME')
    } for l in logfile_records])
    
    # חיבור לפי תעודה
    if log_df.empty:
        return docs_df
    combined = docs_df.merge(log_df, on='תעודה', how='inner')
    
    # הוספת עמודות סוג פעולה, זיהוי מחוודה וחיוב ללקוח
    combined['זיהוי מזוודה'] = combined.apply(
        lambda row: identify_luggage(row['תיאור מוצר']), axis=1
    )
    
    # קביעת סוג פעולה וחיוב
    def calculate_operation_and_charge(row):
        if is_repair_item(row['מקט']):
            repair_price = get_repair_price(row['מספר לקוח'], row['מקט'])
            return 'תיקון', repair_price * row['כמות'] if repair_price else None
        elif row['זיהוי מזוודה']:
            replacement_price = get_replacement_price(row['מספר לקוח'], row['זיהוי מזוודה'])
            return 'החלפה', replacement_price * row['כמות'] if replacement_price else None
        return '', None
    
    combined[['סוג פעולה', 'חיוב ללקוח']] = combined.apply(
        lambda row: pd.Series(calculate_operation_and_charge(row)), axis=1
    )
    
    # הסרת עמודת עזר
    combined = combined.drop('מספר לקוח_log', axis=1)
    
    return combined
