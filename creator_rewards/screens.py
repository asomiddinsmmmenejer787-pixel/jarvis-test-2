"""
creator_rewards/screens.py
--------------------------
Insights skrinshotlari: saqlash, Jarvis (Gemini) orqali raqamlarni o'qish va shubhali belgilarni topish.

Har bir skrinshot aniq bitta videoga bog'lanadi (bloger avval videoni tanlaydi), rasm
DATA_DIR/creator_shots/<video>/<vaqt>.jpg ko'rinishida saqlanadi. Jarvis rasmdan ko'rishlar sonini
va joylangan sanani o'qiydi va ularni bloger yozgan raqam, video sanasi va oldingi
skrinshotlar bilan solishtiradi.
"""

import base64
import json
import logging
import os
import re
import uuid

import requests

from . import rewards as R

log = logging.getLogger("creator_screens")
MAX_IMAGE_BYTES = 6 * 1024 * 1024
API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

PROMPT = (
    "Bu Instagram Reels videosining statistika (Insights) ekrani skrinshoti. Faqat ekranda aniq ko'rinib turgan "
    "raqamlarni o'qi, taxmin qilma. '12,3 тыс', '12.3K', '1,2 млн' kabi yozuvlarni to'liq butun songa aylantir. "
    "Faqat JSON qaytar: {\"is_insights\": bool, \"views\": int|null, \"reach\": int|null, \"likes\": int|null, "
    "\"comments\": int|null, \"shares\": int|null, \"saves\": int|null, \"posted_date\": \"YYYY-MM-DD\"|null, "
    "\"account\": string|null}. views — 'Просмотры'/'Views'/'Ko'rishlar' raqami."
)


def shots_dir():
    import config
    return os.path.join(config.DATA_DIR, "creator_shots")


def save_image(reel_id, data: bytes) -> str:
    if not data:
        raise ValueError("Rasm bo'sh.")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Rasm juda katta (6 MB dan oshmasin).")
    if not (data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n" or data[8:12] == b"WEBP"):
        raise ValueError("Faqat JPG, PNG yoki WEBP rasm yuboring.")
    folder = os.path.join(shots_dir(), str(int(reel_id)))
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{R.utcnow().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}.jpg")
    with open(path, "wb") as f:
        f.write(data)
    return path


def _mime(data):
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def read_insights(data: bytes):
    """Gemini orqali skrinshotdagi raqamlarni o'qiydi. O'qib bo'lmasa None qaytaradi (xato ko'tarmaydi)."""
    import config
    key = (getattr(config, "GEMINI_API_KEY", "") or "").strip()
    if not key:
        return None
    model = os.environ.get("CREATOR_OCR_MODEL", "").strip() or getattr(config, "GEMINI_MODEL", "gemini-2.0-flash")
    payload = {
        "contents": [{"role": "user", "parts": [
            {"inline_data": {"mime_type": _mime(data), "data": base64.b64encode(data).decode()}},
            {"text": PROMPT}]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }
    try:
        r = requests.post(API.format(model=model), json=payload, timeout=40,
                          headers={"Content-Type": "application/json", "x-goog-api-key": key})
        if r.status_code != 200:
            log.warning("Skrinshotni o'qib bo'lmadi: Gemini HTTP %s", r.status_code)
            return None
        parts = r.json()["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        text = re.sub(r"^```(?:json)?|```$", "", text.strip()).strip()
        out = json.loads(text)
        return out if isinstance(out, dict) else None
    except Exception as e:  # tarmoq, JSON yoki xavfsizlik filtri — skrinshot baribir qabul qilinadi
        log.warning("Skrinshotni o'qib bo'lmadi: %s", type(e).__name__)
        return None


def check_flags(reel, previous, ocr, typed_views):
    """Administrator e'tibor berishi kerak bo'lgan holatlar ro'yxati (matn)."""
    flags = []
    ocr = ocr or {}
    ocr_views = ocr.get("views") if isinstance(ocr.get("views"), int) else None
    if ocr and ocr.get("is_insights") is False:
        flags.append("Jarvis: bu Insights ekraniga o'xshamaydi")
    if ocr_views is None:
        flags.append("Jarvis ko'rishlar sonini o'qiy olmadi — rasmni o'zingiz tekshiring")
    if ocr_views is not None and typed_views is not None:
        if abs(ocr_views - typed_views) > max(200, 0.03 * max(ocr_views, typed_views)):
            flags.append(f"Bloger yozgan raqam ({typed_views:,}) rasmdagidan ({ocr_views:,}) farq qiladi".replace(",", " "))
    if ocr.get("posted_date"):
        try:
            posted = R.from_iso(reel["posted_at"]).astimezone(R.TASHKENT).date()
            shot = R.from_iso(ocr["posted_date"] + "T12:00:00+05:00").date()
            if abs((shot - posted).days) > 2:
                flags.append(f"Rasmdagi sana ({shot:%d.%m.%Y}) video ro'yxatga olingan sanadan "
                             f"({posted:%d.%m.%Y}) farq qiladi — boshqa video bo'lishi mumkin")
        except (ValueError, TypeError):
            pass
    views = ocr_views if ocr_views is not None else typed_views
    verified = [p for p in previous if p.get("verified")]
    if views is not None and verified:
        last = verified[-1]
        if views < last["views"]:
            flags.append(f"Ko'rishlar oldingi tasdiqlangandan ({last['views']:,}) kam".replace(",", " "))
        else:
            hours = max(1.0, (R.utcnow() - R.from_iso(last["created_at"])).total_seconds() / 3600)
            per_day = (views - last["views"]) / hours * 24
            if last["views"] > 0 and per_day > 3 * last["views"] and views - last["views"] > 20_000:
                flags.append("Ko'rishlar keskin sakragan — sun'iy ko'rish bo'lishi mumkin")
    if ocr.get("reach") and views and ocr["reach"] > 0 and views / ocr["reach"] > 4:
        flags.append("Ko'rishlar qamrovdan 4 barobardan ko'p — takroriy yoki sun'iy ko'rishlar bo'lishi mumkin")
    return flags
