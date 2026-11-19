"""
youtube_seo.py
--------------
YouTube video uchun sarlavha, tavsif, hashtag va teglarni optimallashtirish.

Ish tartibi (bitta AI chaqiruvi - Gemini limitini tejash uchun):
1. Video ma'lumoti olinadi (sarlavha, tavsif, davomiylik, statistika).
2. Nishadagi eng ko'p ko'rilgan videolar va ularning teglari olinadi (API kalit).
3. AI shu ma'lumot asosida taklif tuzadi (JSON).
4. Natija YouTube qoidalariga moslab tozalanadi (limitlar, taqiqlangan belgilar).
5. Egasi tasdiqlasa va kanal ulangan bo'lsa - videoga yoziladi.

Algoritm xavfsizligi qoidalari:
- faqat videoga aloqador hashtag/teglar (aloqasiz trend teglar = misleading metadata)
- 3-5 ta hashtag, teglar jami 500 belgidan oshmaydi
- sarlavha 100 belgigacha, yolg'on va'da (clickbait) yo'q
- raqobatchi kanal yoki brend nomlari teg sifatida ishlatilmaydi
"""

import json
import logging
import os
import re
import secrets
import threading
import time

import config
import youtube_api as yt

log = logging.getLogger("youtube_seo")

TITLE_MAX = 100
DESC_MAX = 5000
TAGS_MAX_CHARS = 500
HASHTAG_MAX = 5
SHORTS_MAX_SEC = 180
PENDING_KEEP_SEC = 3 * 24 * 3600

_lock = threading.Lock()

SYSTEM = """Sen YouTube SEO mutaxassisisan. O'zbekiston auditoriyasi uchun ishlaysan.
Vazifang: berilgan video uchun qidiruvda va tavsiyalarda yaxshiroq chiqadigan
sarlavha, tavsif, hashtag va teglarni tuzish. Faqat JSON qaytar.

QAT'IY QOIDALAR (YouTube algoritmi va qoidalarini buzmaslik uchun):
1. Faqat videoning haqiqiy mazmuniga aloqador so'zlar. Aloqasiz trend, mashhur
   blogger, boshqa brend yoki raqobatchi kanal nomlarini ISHLATMA - bu YouTube'da
   "misleading metadata" hisoblanadi va videoni pasaytiradi yoki o'chiradi.
2. Sarlavha: 100 belgigacha, eng muhim kalit so'z boshida, odamni qiziqtirsin,
   lekin videoda yo'q narsani va'da qilmasin. Hammasini KATTA harf bilan yozma.
3. Tavsif: birinchi 2 qator eng muhim (qidiruvda shu ko'rinadi) - kalit so'z va
   foyda. Keyin 3-6 qator qisqa ma'lumot. Kalit so'zlarni takror-takror tiqma.
   Tabiiy, odam yozgandek o'zbek tilida (lotin). Oxirida hashtaglar.
   Narx, manzil yoki telefon faqat egasi bergan bo'lsa yoziladi, o'ylab topma.
4. Hashtag: 3-5 ta, aniq va nishaviy. Shorts bo'lsa bittasi #shorts.
5. Teglar (tags): 8-15 ta, umumiy uzunligi 450 belgidan kam. Avval aniq iboralar,
   keyin sinonimlar, o'zbekcha, ruscha va keng tarqalgan xato yozilishlar.
6. Raqobatchilar ro'yxati faqat qaysi so'zlar ishlayotganini tushunish uchun.
   Ularning sarlavhasini ko'chirma.

JSON tuzilishi:
{
  "title": "asosiy sarlavha",
  "alt_titles": ["2-variant", "3-variant"],
  "description": "to'liq tavsif (hashtaglar bilan)",
  "hashtags": ["#...", "#..."],
  "tags": ["...", "..."],
  "keywords": ["asosiy kalit so'zlar, 3-5 ta"],
  "why": "nega aynan shunday - 2-3 jumla, o'zbekcha",
  "tips": ["oblojka yoki birinchi soniyalar uchun 1-3 ta qisqa maslahat"]
}"""


# ---------- tozalash va cheklovlar ----------

def _clean_text(text, limit):
    text = str(text or "").replace("<", "").replace(">", "").strip()
    return text[:limit].rstrip()


def _norm_hashtag(h):
    h = re.sub(r"[^\w]", "", str(h or "").replace("#", ""), flags=re.UNICODE)
    return f"#{h}" if h else ""


def sanitize(raw, is_short=False):
    """AI javobini YouTube limitlariga moslaydi."""
    raw = raw if isinstance(raw, dict) else {}
    title = _clean_text(raw.get("title"), TITLE_MAX)
    alts = [_clean_text(t, TITLE_MAX) for t in (raw.get("alt_titles") or []) if str(t or "").strip()][:2]

    hashtags, seen = [], set()
    for h in raw.get("hashtags") or []:
        h = _norm_hashtag(h)
        if h and h.lower() not in seen:
            seen.add(h.lower())
            hashtags.append(h)
    if is_short and "#shorts" not in seen:
        hashtags.insert(0, "#shorts")
    hashtags = hashtags[:HASHTAG_MAX]

    desc = _clean_text(raw.get("description"), DESC_MAX)
    # Tavsifdagi barcha hashtaglarni olib tashlab, oxiriga faqat tasdiqlangan 3-5 tasini qo'yamiz
    desc = re.sub(r"(?:^|\s)#[\w]+", "", desc, flags=re.UNICODE).rstrip()
    if hashtags:
        desc = (desc + "\n\n" + " ".join(hashtags)).strip()
    desc = desc[:DESC_MAX]

    tags, total, seen = [], 0, set()
    for t in raw.get("tags") or []:
        t = re.sub(r"[<>,\"]", "", str(t or "")).strip()[:100]
        if not t or t.lower() in seen:
            continue
        # YouTube bo'sh joyli tegni qo'shtirnoq bilan hisoblaydi (+2)
        cost = len(t) + (2 if " " in t else 0) + (1 if tags else 0)
        if total + cost > TAGS_MAX_CHARS:
            break
        seen.add(t.lower())
        tags.append(t)
        total += cost

    return {
        "title": title,
        "alt_titles": alts,
        "description": desc,
        "hashtags": hashtags,
        "tags": tags,
        "keywords": [str(k).strip() for k in (raw.get("keywords") or []) if str(k).strip()][:5],
        "why": _clean_text(raw.get("why"), 600),
        "tips": [_clean_text(t, 200) for t in (raw.get("tips") or []) if str(t or "").strip()][:3],
    }


# ---------- AI uchun ma'lumot ----------

def _fmt_views(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def build_user_message(video, niche, hint=""):
    sn = video.get("snippet") or {}
    st = video.get("statistics") or {}
    dur = yt.iso_duration_sec((video.get("contentDetails") or {}).get("duration"))
    is_short = 0 < dur <= SHORTS_MAX_SEC
    lines = [
        "VIDEO:",
        f"- Turi: {'Shorts (vertikal, qisqa)' if is_short else 'oddiy video'}, davomiyligi {dur} soniya",
        f"- Hozirgi sarlavha: {sn.get('title', '')}",
        f"- Hozirgi tavsif: {(sn.get('description') or '')[:1500] or '(bo`sh)'}",
        f"- Hozirgi teglar: {', '.join(sn.get('tags') or []) or '(yo`q)'}",
        f"- Ko'rishlar: {st.get('viewCount', 0)}",
    ]
    if hint:
        lines.append(f"\nEGASINING IZOHI (video nima haqida, mahsulot, narx va h.k.): {hint}")
    if niche:
        lines.append("\nNISHADAGI TOP VIDEOLAR (oxirgi 6 oy, O'zbekiston, ko'rishlar bo'yicha):")
        for i, v in enumerate(niche[:10], 1):
            kind = "Shorts" if 0 < v["duration_sec"] <= SHORTS_MAX_SEC else "video"
            tags = ", ".join(v["tags"][:8]) or "-"
            lines.append(f"{i}. [{_fmt_views(v['views'])} ko'rish, {kind}] {v['title']} | teglar: {tags}")
    else:
        lines.append("\nNishadagi top videolar topilmadi - umumiy SEO bilimingga tayan.")
    return "\n".join(lines), is_short


def niche_query(video, hint=""):
    """Raqobatchilarni qidirish uchun so'rov: egasining izohi yoki video sarlavhasi."""
    base = hint or (video.get("snippet") or {}).get("title", "")
    base = re.sub(r"#\w+", " ", base)
    base = re.sub(r"[^\w\s'ʻ’-]", " ", base, flags=re.UNICODE)
    words = [w for w in base.split() if len(w) > 2][:6]
    return " ".join(words)


# ---------- asosiy ish ----------

def optimize(ai, video_id, hint="", with_niche=True):
    """Video uchun SEO taklif tayyorlaydi va kutish ro'yxatiga qo'yadi. Qaytaradi: item dict."""
    video = yt.get_video(video_id)
    niche = []
    if with_niche:
        try:
            niche = yt.search_niche(niche_query(video, hint))
        except yt.YouTubeError as e:
            if e.fatal:
                raise
            log.warning("Nisha qidiruvi o'tkazib yuborildi: %s", e)

    msg, is_short = build_user_message(video, niche, hint)
    raw = ai.generate_json(SYSTEM, msg, 0.7) if hasattr(ai, "generate_json") else None
    if raw is None and hasattr(ai, "generate_text"):
        text = ai.generate_text(SYSTEM, msg)
        m = re.search(r"\{.*\}", text or "", re.S)
        try:
            raw = json.loads(m.group(0)) if m else None
        except ValueError:
            raw = None
    if not isinstance(raw, dict) or not raw.get("title"):
        raise yt.YouTubeError("AI javob bermadi. Birozdan keyin qayta urinib ko'ring.")

    result = sanitize(raw, is_short=is_short)
    sn = video.get("snippet") or {}
    item = {
        "id": secrets.token_hex(4),
        "video_id": video_id,
        "old_title": sn.get("title", ""),
        "channel_id": sn.get("channelId", ""),
        "is_short": is_short,
        "hint": hint,
        "niche_count": len(niche),
        "niche_top": [{"title": v["title"], "views": v["views"]} for v in niche[:3]],
        "result": result,
        "created": time.time(),
    }
    _put(item)
    return item


# ---------- kutish ro'yxati (tasdiq tugmalari uchun) ----------

def _pending_path():
    return os.path.join(config.DATA_DIR, "youtube_pending.json")


def _load_pending():
    try:
        with open(_pending_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_pending(data):
    path = _pending_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(path + ".tmp", path)
    except OSError as e:
        log.error("youtube_pending.json saqlab bo'lmadi: %s", type(e).__name__)


def _put(item):
    with _lock:
        data = _load_pending()
        now = time.time()
        data = {k: v for k, v in data.items() if now - v.get("created", 0) < PENDING_KEEP_SEC}
        data[item["id"]] = item
        _save_pending(data)


def get_pending(pid):
    return _load_pending().get(pid)


def pop_pending(pid):
    with _lock:
        data = _load_pending()
        item = data.pop(pid, None)
        _save_pending(data)
        return item


def apply(pid, title_index=0):
    """Tasdiqlangan taklifni videoga yozadi. title_index: 0 - asosiy, 1/2 - muqobil sarlavha."""
    item = get_pending(pid)
    if not item:
        return "Bu taklif eskirgan. Video havolasini qayta yuboring."
    if not yt.is_connected():
        return "Kanal ulanmagan. Matnni Studio'ga nusxalang yoki /ytulash bilan ulang."
    r = item["result"]
    titles = [r["title"]] + r.get("alt_titles", [])
    title = titles[title_index] if 0 <= title_index < len(titles) else r["title"]
    yt.update_video_metadata(item["video_id"], title=title, description=r["description"], tags=r["tags"])
    pop_pending(pid)
    return f"✅ Videoga yozildi: «{title}»"


# ---------- Telegram matni ----------

def suggestion_text(item):
    r = item["result"]
    url = f"https://youtu.be/{item['video_id']}"
    lines = [f"🎬 YouTube SEO {'(Shorts)' if item.get('is_short') else ''}".strip(), url, ""]
    lines.append(f"Eski sarlavha: {item.get('old_title') or '-'}")
    lines.append(f"\n1️⃣ Yangi sarlavha:\n{r['title']}")
    for i, t in enumerate(r.get("alt_titles", []), 2):
        lines.append(f"{i}️⃣ {t}")
    lines.append(f"\n📝 Tavsif:\n{r['description']}")
    if r["tags"]:
        lines.append(f"\n🏷 Teglar (Studio → Show more → Tags):\n{', '.join(r['tags'])}")
    if r.get("why"):
        lines.append(f"\n💡 Nega shunday: {r['why']}")
    if r.get("tips"):
        lines.append("\n🎯 Maslahat:\n" + "\n".join(f"• {t}" for t in r["tips"]))
    if item.get("niche_count"):
        top = item.get("niche_top") or []
        lines.append(f"\n📊 Nishadagi {item['niche_count']} ta top video tahlil qilindi.")
        if top:
            lines.append("Eng kuchlisi: " + top[0]["title"][:80] + f" ({_fmt_views(top[0]['views'])})")
    return "\n".join(lines)
