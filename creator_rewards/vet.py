"""
creator_rewards/vet.py
----------------------
Blogerning Instagram akkauntini ochiq ma'lumotlar asosida tekshirish va 10 ballik faollik bahosi.

Manbalar (birinchisi ishlamasa ikkinchisi):
  1) Instagram Business Discovery (Facebook Login orqali): obunachilar, postlar, oxirgi 25 ta post sanasi,
     ko'rishlar, layklar, izohlar. Kerak: IG_BD_TOKEN va IG_BD_USER_ID muhit o'zgaruvchilari.
  2) Bloger yuborgan profil skrinshoti (Reels bo'limi): Jarvis obunachilar va videolar ko'rishlarini o'qiydi.

Baho — Tashkilotchining ichki vositasi. Yakuniy qarorni administrator qabul qiladi.
"""

import base64
import json
import logging
import os
import re
import statistics
from datetime import datetime, timezone

import requests

log = logging.getLogger("creator_vet")
GRAPH = "https://graph.facebook.com/v23.0"
FIELDS = ("business_discovery.username({u}){{username,name,followers_count,follows_count,media_count,"
          "media.limit(25){{timestamp,media_type,media_product_type,like_count,comments_count,view_count,permalink}}}}")

PROFILE_PROMPT = (
    "Bu Instagram profil sahifasining (Reels bo'limi) skrinshoti. Faqat ekranda aniq ko'rinadigan raqamlarni o'qi. "
    "'12,3 тыс', '12.3K', '1,2 млн' kabi yozuvlarni to'liq butun songa aylantir. Faqat JSON qaytar: "
    "{\"username\": string|null, \"followers\": int|null, \"following\": int|null, \"posts\": int|null, "
    "\"reel_views\": [int, ...]} — reel_views: to'rdagi har bir video ustidagi ko'rishlar soni, chapdan o'ngga, yuqoridan pastga."
)


# ---------------- ma'lumot yig'ish ----------------

def bd_configured():
    return bool((os.environ.get("IG_BD_TOKEN") or "").strip() and (os.environ.get("IG_BD_USER_ID") or "").strip())


def fetch_business_discovery(username):
    """Ochiq ma'lumotlarni Instagram API'dan oladi. Sozlanmagan yoki xato bo'lsa None."""
    if not bd_configured():
        return None
    try:
        r = requests.get(f"{GRAPH}/{os.environ['IG_BD_USER_ID'].strip()}",
                         params={"fields": FIELDS.format(u=username),
                                 "access_token": os.environ["IG_BD_TOKEN"].strip()}, timeout=25)
        data = r.json()
        if r.status_code != 200 or "business_discovery" not in data:
            log.warning("Business Discovery javobi: %s", (data.get("error") or {}).get("message", r.status_code))
            return None
        bd = data["business_discovery"]
        posts = []
        for m in (bd.get("media") or {}).get("data", []):
            posts.append({"at": m.get("timestamp"), "likes": m.get("like_count"), "comments": m.get("comments_count"),
                          "views": m.get("view_count"), "reel": m.get("media_product_type") == "REELS",
                          "url": m.get("permalink")})
        return {"source": "instagram_api", "username": bd.get("username"), "followers": bd.get("followers_count"),
                "following": bd.get("follows_count"), "media_count": bd.get("media_count"), "posts": posts}
    except Exception as e:
        log.warning("Business Discovery ishlamadi: %s", type(e).__name__)
        return None


def read_profile_screenshot(data: bytes):
    """Profil skrinshotidan raqamlarni Gemini orqali o'qiydi. O'qib bo'lmasa None."""
    import config
    from .screens import _mime
    key = (getattr(config, "GEMINI_API_KEY", "") or "").strip()
    if not key:
        return None
    model = os.environ.get("CREATOR_OCR_MODEL", "").strip() or getattr(config, "GEMINI_MODEL", "gemini-2.0-flash")
    try:
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"Content-Type": "application/json", "x-goog-api-key": key}, timeout=40,
            json={"contents": [{"role": "user", "parts": [
                {"inline_data": {"mime_type": _mime(data), "data": base64.b64encode(data).decode()}},
                {"text": PROFILE_PROMPT}]}],
                "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}})
        if r.status_code != 200:
            return None
        text = "".join(p.get("text", "") for p in r.json()["candidates"][0]["content"]["parts"] if not p.get("thought"))
        out = json.loads(re.sub(r"^```(?:json)?|```$", "", text.strip()).strip())
        views = [int(v) for v in (out.get("reel_views") or []) if isinstance(v, (int, float))]
        return {"source": "screenshot", "username": out.get("username"), "followers": out.get("followers"),
                "following": out.get("following"), "media_count": out.get("posts"),
                "posts": [{"views": v, "reel": True} for v in views]}
    except Exception as e:
        log.warning("Profil skrinshotini o'qib bo'lmadi: %s", type(e).__name__)
        return None


# ---------------- baholash ----------------

def _band(value, bands):
    """bands: [(chegara, ball), ...] kamayish tartibida; value >= chegara bo'lsa shu ball."""
    for limit, pts in bands:
        if value >= limit:
            return pts
    return bands[-1][1] if bands else 0


def score(metrics, now=None):
    """(ball 0–10, qismlar ro'yxati, ogohlantirishlar). Ma'lumot yetmagan qism hisobga olinmaydi."""
    now = now or datetime.now(timezone.utc)
    parts, warns = [], []
    f = metrics.get("followers") or 0
    posts = metrics.get("posts") or []
    reels = [p for p in posts if p.get("reel")] or posts
    views = [p["views"] for p in reels if isinstance(p.get("views"), int) and p["views"] >= 0]
    eng = [(p.get("likes") or 0) + (p.get("comments") or 0) for p in posts
           if isinstance(p.get("likes"), int) or isinstance(p.get("comments"), int)]
    dates = []
    for p in posts:
        try:
            dates.append(datetime.fromisoformat(str(p["at"]).replace("+0000", "+00:00").replace("Z", "+00:00")))
        except (KeyError, ValueError, TypeError):
            pass

    def add(name, pts, weight, detail):
        parts.append({"name": name, "points": round(pts, 1), "weight": weight, "detail": detail})

    if f:
        pts = (10 if 10_000 <= f <= 100_000 else 8 if 100_000 < f <= 200_000 else 6 if 5_000 <= f < 10_000
               else 7 if f > 200_000 else 3)
        add("Auditoriya hajmi", pts, 1, f"{f:,} obunachi".replace(",", " "))
        if f < 10_000:
            warns.append("Obunachilar 10 000 dan kam — dastur talabidan past")
    if views and f:
        med = statistics.median(views)
        ratio = med / f
        add("Ko'rishlar / obunachi", _band(ratio, [(0.5, 10), (0.3, 8), (0.15, 6), (0.07, 4), (0.03, 2), (0, 1)]), 3,
            f"o'rtacha (mediana) {int(med):,} ko'rish — obunachilarning {ratio:.0%}".replace(",", " "))
        if ratio < 0.03:
            warns.append("Ko'rishlar obunachilarga nisbatan juda kam — obunachilar sotib olingan yoki nofaol bo'lishi mumkin")
    elif views:
        add("Ko'rishlar", _band(statistics.median(views), [(30_000, 10), (15_000, 8), (8_000, 6), (3_000, 4), (0, 2)]), 3,
            f"o'rtacha (mediana) {int(statistics.median(views)):,} ko'rish".replace(",", " "))
    if eng and views:
        er = statistics.median(eng) / max(1, statistics.median(views))
        add("Faollik (layk + izoh / ko'rish)", _band(er, [(0.08, 10), (0.05, 8), (0.03, 6), (0.015, 4), (0, 2)]), 2,
            f"{er:.1%}")
        if er < 0.01 and statistics.median(views) > 5_000:
            warns.append("Ko'rishlar ko'p, lekin layk va izoh juda kam — sun'iy ko'rishlar bo'lishi mumkin")
    elif eng and f:
        er = statistics.median(eng) / f
        add("Faollik (layk + izoh / obunachi)", _band(er, [(0.03, 10), (0.02, 8), (0.01, 6), (0.005, 4), (0, 2)]), 2,
            f"{er:.1%}")
    if dates:
        last = max(dates)
        days = (now - last).days
        add("So'nggi post", _band(-days, [(-3, 10), (-7, 8), (-14, 6), (-30, 3), (-10**6, 0)]), 1,
            f"{days} kun oldin ({last:%d.%m.%Y})")
        if days > 30:
            warns.append("Oxirgi 30 kunda post yo'q — akkaunt faol emas")
        recent = sum(1 for d in dates if (now - d).days <= 30)
        add("Muntazamlik", _band(recent, [(12, 10), (8, 8), (4, 6), (2, 4), (1, 2), (0, 0)]), 2,
            f"oxirgi 30 kunda {recent} ta post")
    if len(views) >= 4:
        med = max(1, statistics.median(views))
        spike = max(views) / med
        add("Barqarorlik", _band(-spike, [(-5, 10), (-10, 7), (-30, 4), (-10**9, 2)]), 1,
            f"eng ko'p ko'rilgan video medianadan {spike:.0f} barobar")
        if spike > 30:
            warns.append("Bitta video boshqalardan keskin ko'p — tasodifiy viral yoki sotib olingan bo'lishi mumkin")
    total_w = sum(p["weight"] for p in parts)
    value = round(sum(p["points"] * p["weight"] for p in parts) / total_w, 1) if total_w else None
    if len(parts) < 3:
        warns.append("Ma'lumot kam — bahoga ehtiyot bo'lib qarang")
    return value, parts, warns


def report_text(username, metrics, value, parts, warns):
    src = {"instagram_api": "Instagram ochiq ma'lumotlari", "screenshot": "profil skrinshoti"}.get(
        (metrics or {}).get("source"), "—")
    lines = [f"📊 @{username} — faollik bahosi: {value if value is not None else '—'} / 10", f"Manba: {src}"]
    for p in parts:
        lines.append(f"• {p['name']}: {p['points']}/10 — {p['detail']}")
    for w in warns:
        lines.append(f"⚠️ {w}")
    return "\n".join(lines)


def evaluate(username, metrics):
    if not metrics:
        return None
    value, parts, warns = score(metrics)
    return {"score": value, "parts": parts, "warnings": warns, "source": metrics.get("source"),
            "followers": metrics.get("followers"), "text": report_text(username, metrics, value, parts, warns)}
