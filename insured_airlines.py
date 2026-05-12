# -*- coding: utf-8 -*-
"""
insured_airlines.py — רשימת חברות-תעופה שאנחנו מבטחים את המזוודות שלהן.

רק נחיתות של החברות האלה רלוונטיות לתחזיות תיקונים — שאר הטיסות לא
מייצרות לקוחות.

הרשימה אומתה מול ה-dropdown של IAA flight-board ב-2026-05. חברות
שלא מופיעות שם הוסרו (פשטו רגל / לא טסות כרגע ל-בן-גוריון).

לעדכון: הוסף קוד IATA + שם, וודא שהוא קיים ב-IAA dropdown.
"""

# IATA → display name (לקריאות בלוגים ו-UI)
INSURED_AIRLINES: dict[str, str] = {
    'LY': 'EL AL ISRAEL AIRLINES',
    'AF': 'AIR FRANCE',
    'KL': 'K.L.M.',
    'DL': 'DELTA AIRLINES',
    'HU': 'HAINAN AIRLINES',
    'A3': 'AEGEAN AIRLINES',
    'BT': 'AIR BALTIC',
    'EW': 'EUROWINGS',
    'TP': 'AIR PORTUGAL',
    'UA': 'UNITED AIRLINES',
    'LO': 'LOT POLISH AIRLINES',
}


def insured_codes() -> list[str]:
    return list(INSURED_AIRLINES.keys())


def is_insured(iata_code: str) -> bool:
    return iata_code in INSURED_AIRLINES
