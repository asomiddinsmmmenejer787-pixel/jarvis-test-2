"""
creator_rewards/fx.py
---------------------
AQSh dollarining O'zbekiston Markaziy banki rasmiy kursi (200$ mukofoti uchun).
Kurs cbu.uz'dan olinadi va 6 soat keshlanadi. Internet bo'lmasa — oxirgi olingan kurs;
umuman bo'lmasa — USD_RATE muhit o'zgaruvchisi (qo'lda). Hech biri bo'lmasa None.
"""

import logging
import os
import time

import requests

log = logging.getLogger("creator_fx")
CBU_URL = "https://cbu.uz/uz/arkhiv-kursov-valyut/json/USD/"
TTL = 6 * 3600
_cache = {"rate": None, "date": None, "at": 0.0}


def usd_rate(force=False):
    """(kurs, sana) — masalan (12650.43, '23.11.2026'). Topilmasa (None, None)."""
    if not force and _cache["rate"] and time.time() - _cache["at"] < TTL:
        return _cache["rate"], _cache["date"]
    try:
        r = requests.get(CBU_URL, timeout=15)
        r.raise_for_status()
        row = r.json()[0]
        rate = float(str(row["Rate"]).replace(",", "."))
        if rate > 0:
            _cache.update(rate=rate, date=row.get("Date"), at=time.time())
            return rate, row.get("Date")
    except Exception as e:
        log.warning("MB kursini olib bo'lmadi: %s", type(e).__name__)
    if _cache["rate"]:
        return _cache["rate"], _cache["date"]
    manual = (os.environ.get("USD_RATE") or "").strip().replace(",", ".")
    try:
        return (float(manual), "qo'lda") if manual else (None, None)
    except ValueError:
        return None, None


def cached_rate():
    """Tarmoqqa chiqmasdan: kabinetdagi taxminiy hisob uchun."""
    if _cache["rate"]:
        return _cache["rate"]
    manual = (os.environ.get("USD_RATE") or "").strip().replace(",", ".")
    try:
        return float(manual) if manual else None
    except ValueError:
        return None
