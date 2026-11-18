"""
brain.py
--------
Jarvis'ning o'rganish xotirasi.

- KO'RSATMALAR (lessons): egasi Telegram'da "bunday javob berma", "belgilanganlarga ham murojaat qil"
  kabi tanbeh yozsa, Jarvis uni doimiy qoida sifatida saqlaydi va komment hamda direkt javoblarida
  har safar unga amal qiladi.
- JURNAL: Jarvis nima qilganini (komentga javob, direkt, tasdiqqa yuborish) yozib boradi, egasi
  "nima qilding?", "nega javob bermading?" desa shunga tayanib javob beradi.
- think(): egasining xabarini tushunadi: oddiy suhbatmi, yangi ko'rsatmami, mahsulot faktimi yoki
  harakat (masalan, oxirgi postdagi javobsiz komentlarni qayta tekshirish) kerakmi.
"""

import json
import logging
import os
import threading
import time
from datetime import datetime

import catalog
import config

log = logging.getLogger("brain")
_lock = threading.Lock()
MAX_LESSONS = 60
MAX_JOURNAL = 400


# ---------- ko'rsatmalar ----------

def get_lessons():
    return list(catalog.load().get("lessons", []))


def add_lesson(text):
    text = catalog._clean(text, 400)
    if not text:
        raise ValueError("bo'sh ko'rsatma")
    data = catalog.load()
    lessons = data.setdefault("lessons", [])
    if text not in lessons:
        lessons.append(text)
        data["lessons"] = lessons[-MAX_LESSONS:]
        catalog.save(data)
    return data["lessons"]


def delete_lesson(index):
    data = catalog.load()
    lessons = data.setdefault("lessons", [])
    if not 0 <= index < len(lessons):
        raise IndexError(index)
    removed = lessons.pop(index)
    catalog.save(data)
    return removed


def lessons_block():
    lessons = get_lessons()
    if not lessons:
        return ""
    return ("EGASINING KO'RSATMALARI (doim amal qil; xavfsizlik qoidalari, narxni ochiq yozmaslik va "
            "FAKTLARdan tashqari ma'lumot to'qimaslik baribir saqlanadi):\n"
            + "\n".join(f"- {x}" for x in lessons))


# ---------- jurnal ----------

def _jpath():
    return os.path.join(config.DATA_DIR, "journal.json")


def _jload():
    try:
        with open(_jpath(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


def log_entry(text):
    with _lock:
        items = _jload()
        items.append({"t": int(time.time()), "x": str(text)[:300]})
        items = items[-MAX_JOURNAL:]
        try:
            os.makedirs(config.DATA_DIR, exist_ok=True)
            with open(_jpath() + ".tmp", "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False)
            os.replace(_jpath() + ".tmp", _jpath())
        except OSError as e:
            log.warning("Jurnalni saqlab bo'lmadi: %s", e)


def log_event(ev):
    """auto_reply / dm_chat voqeasini jurnalga qisqa yozadi."""
    t = ev.get("type")
    who = "@" + (ev.get("username") or "?")
    if t == "sent":
        log_entry(f"Komentga javob: {who} «{ev.get('text', '')[:80]}» → {ev.get('reply', '')[:120]}")
    elif t == "approval":
        log_entry(f"Komment egasiga yuborildi ({ev.get('reason', '')}): {who} «{ev.get('text', '')[:80]}»")
    elif t == "dm_sent":
        log_entry(f"Komentdan keyin direkt: {who} → {ev.get('text', '')[:120]}")
    elif t == "chat_sent":
        log_entry(f"Direktda javob: {who} «{ev.get('message', '')[:80]}» → {ev.get('text', '')[:120]}")
    elif t == "chat_approval":
        log_entry(f"Direkt egasiga topshirildi ({ev.get('reason', '')}): {who} «{ev.get('message', '')[:80]}»")
    elif t in ("dm_error", "chat_error"):
        log_entry(f"Direkt yuborilmadi: {who} ({ev.get('error', '')})")


def journal_text(n=30):
    rows = []
    for it in _jload()[-n:]:
        when = datetime.fromtimestamp(it["t"], catalog.TASHKENT).strftime("%d.%m %H:%M")
        rows.append(f"{when} {it['x']}")
    return "\n".join(rows)


# ---------- egasining xabarini tushunish ----------

THINK_PROMPT = """
Sen Jarvissan: Instagram sahifasini boshqaradigan yordamchi (komentlarga va direktga javob beruvchi).
Do'kon egasi senga Telegram'da yozdi. Uning xabarini tushun va nima qilish kerakligini hal qil.

Senga beriladi: HOLAT (hozirgi sozlamalar), JURNAL (oxirgi qilgan ishlaring), KO'RSATMALAR (egasi oldin
bergan qoidalar), OXIRGI POST, SUHBAT (oxirgi gaplashuv) va EGASINING XABARI.

TURLAR (kind):
- "chat": kontent so'rovi (ssenariy, g'oya, caption, tahlil), umumiy savol yoki suhbat. Boshqa hech narsa qilma.
- "work": egasi komment/direkt ishingiz haqida yozdi: tanbeh, yangi qoida, "nega javob bermading",
  "qayta tekshir", "javoblar uzun", "nima qilding". Shunda:
  * lesson: kelajakdagi barcha javoblar uchun qisqa, aniq qoida (buyruq shaklida, 1-2 gap). Egasi
    qanday ishlashni o'zgartirishni so'ragan yoki tanbeh bergan bo'lsa to'ldir, aks holda bo'sh satr.
    Masalan: "Kommentda kimdir do'stini belgilasa, javobni ikkalasiga qarat va qisqa yoz."
  * fact: mahsulot, narx, manzil, tanlov shartlari kabi do'kon ma'lumoti bo'lsa (bo'lmasa bo'sh satr).
  * actions: kerakli harakatlar ro'yxati, faqat quyidagilardan:
    {"type": "recheck_comments", "posts": 1}  - oxirgi N ta (1-5) postdagi javobsiz qolgan komentlarni
       qayta ko'rib, javob yozish (egasi "javob berilmagan komentlar bor", "qayta tekshir" desa).
    {"type": "set_limit", "value": 500}  - komentlarga kunlik avtomatik javoblar limiti (10-1000).
    {"type": "comment_mode", "mode": "on|off|test"}  - komentga avtomatik javobni yoqish/o'chirish/test.
    {"type": "dm_mode", "mode": "auto|approve|off"}  - direkt suhbatlarga javob rejimi.
    {"type": "set_policy", ...}  - limitni tejash qoidalari, faqat o'zgarayotgan kalitlarni yoz:
       "low_limit": belgilash (@) va maqtov komentlariga kunlik javob soni (0-1000; 0 = umuman yozma),
       "per_user_day": bir odamga kuniga nechta shunday javob (1-10),
       "short_ratio": faqat emoji yoki 1-2 so'zli komentlarning necha foiziga javob (0-100),
       "reserve_pct": asosiy limitning necha foizi faqat savol/narx/shikoyatga saqlanadi (0-90),
       "skip": javob yozilmaydigan turlar ro'yxati, to'liq yangi ro'yxat
               (tag=belgilash, positive=maqtov, question=savol, price=narx, complaint=shikoyat).
       Masalan "maqtovlarga yozma" -> {"type":"set_policy","skip":["positive"]};
       "belgilashga 50 ta yetadi" -> {"type":"set_policy","low_limit":50}.
  * reply: egasiga qisqa javob: nimani tushunding, nima qilasan. JURNALga tayanib gapir, to'qima.
    Xatoni tan ol, uzr so'rab cho'zma.
  * HALOLLIK: sozlamani o'zgartirish faqat actions orqali bo'ladi. "Bajardim", "oshirdim" dema; natijani tizim
    o'zi qo'shadi. So'ralgan ishni yuqoridagi harakatlar bilan qilib bo'lmasa, buni ochiq ayt
    ("buni hozir o'zim qila olmayman").
  Sozlama so'rovi (limit, rejim) ham kind="work".

Javob faqat JSON:
{"kind": "chat|work", "reply": "...", "lesson": "...", "fact": "...", "actions": []}
""".strip()


def think(ai, owner_text, status_text="", last_post_text="", history_text=""):
    lessons = get_lessons()
    msg = (f"HOLAT:\n{status_text or '-'}\n\nJURNAL:\n{journal_text(30) or '-'}\n\n"
           f"KO'RSATMALAR:\n" + ("\n".join(f"- {x}" for x in lessons) or "-")
           + f"\n\nOXIRGI POST:\n{last_post_text or '-'}\n\nSUHBAT:\n{history_text or '-'}"
           + f"\n\nEGASINING XABARI:\n{owner_text[:1500]}")
    res = ai.generate_json(THINK_PROMPT, msg, temperature=0.2)
    out = {"kind": "chat", "reply": "", "lesson": "", "fact": "", "actions": []}
    if not isinstance(res, dict):
        return out
    out["kind"] = "work" if res.get("kind") == "work" else "chat"
    out["reply"] = str(res.get("reply") or "").strip()[:1500]
    out["lesson"] = catalog._clean(res.get("lesson"), 400) if isinstance(res.get("lesson"), str) else ""
    out["fact"] = catalog._clean(res.get("fact"), 400) if isinstance(res.get("fact"), str) else ""
    acts = []
    for a in res.get("actions") or []:
        if not isinstance(a, dict):
            continue
        t = a.get("type")
        try:
            if t == "recheck_comments":
                acts.append({"type": t, "posts": max(1, min(5, int(a.get("posts") or 1)))})
            elif t == "set_limit":
                acts.append({"type": t, "value": max(10, min(1000, int(a.get("value"))))})
            elif t == "comment_mode" and a.get("mode") in ("on", "off", "test"):
                acts.append({"type": t, "mode": a["mode"]})
            elif t == "dm_mode" and a.get("mode") in ("auto", "approve", "off"):
                acts.append({"type": t, "mode": a["mode"]})
            elif t == "set_policy":
                ch = {k: int(a[k]) for k in ("low_limit", "per_user_day", "short_ratio", "reserve_pct")
                      if a.get(k) is not None and str(a[k]).lstrip("-").isdigit()}
                if isinstance(a.get("skip"), list):
                    ch["skip"] = [str(x) for x in a["skip"]]
                if ch:
                    acts.append({"type": t, "changes": ch})
        except (TypeError, ValueError):
            continue
    out["actions"] = acts[:4]
    return out


CAT_UZ = {"tag": "belgilash", "positive": "maqtov", "question": "savol", "price": "narx",
          "complaint": "shikoyat", "negative": "salbiy", "competitor": "raqobatchi"}


def policy_text(st):
    """Tejash qoidalari va bugungi sarf, oddiy tilda."""
    p = st.get("policy") or {}
    cats = st.get("today_cats") or {}
    lim = st["limit_day"]
    reserve = round(lim * p.get("reserve_pct", 0) / 100)
    skip = ", ".join(CAT_UZ.get(x, x) for x in p.get("skip") or []) or "yo'q"
    used = ", ".join(f"{CAT_UZ.get(k, k)} {v}" for k, v in sorted(cats.items(), key=lambda x: -x[1])) or "hali yo'q"
    return (f"Kunlik limit: {st['sent_today']}/{lim}\n"
            f"Bugun: {used}\n"
            f"Belgilash va maqtovlarga: {st.get('low_today', 0)}/{p.get('low_limit')} (alohida limit)\n"
            f"Faqat savol/narx/shikoyatga zaxira: {reserve} ta ({p.get('reserve_pct')}%)\n"
            f"Bir odamga kuniga: {p.get('per_user_day')} ta (belgilash/maqtov)\n"
            f"Faqat emoji yoki 1-2 so'zli komentlarning {p.get('short_ratio')}% iga javob\n"
            f"Umuman javob yozilmaydi: {skip}\n"
            f"Bugun tejaldi (AI'ga yuborilmadi): {st.get('saved_today', 0)} ta")


def build_context(responder, chat, memory):
    """think() uchun holat, oxirgi post va suhbat matnlari."""
    import instagram_api as ig
    st = []
    if responder is not None:
        r = responder.status()
        st.append(f"Komentga javob: {r['mode']}, bugun {r['sent_today']}/{r['limit_day']} (kunlik limit "
                  f"{r['limit_day']}), ruxsat kutayotgan {r['pending']}, komentdan keyin direkt: {r['dm_mode']}")
        if r.get("policy"):
            st.append("TEJASH QOIDALARI:\n" + policy_text(r))
        if r.get("last_run"):
            st.append("Oxirgi komment tekshiruvi: " + json.dumps(r["last_run"], ensure_ascii=False))
    if chat is not None:
        st.append(f"Direkt suhbat rejimi: {chat.mode}")
    last_post = ""
    try:
        posts = ig.get_recent_posts(1)
        if posts:
            p = posts[0]
            last_post = (f"{catalog._post_time(p)}, {p.get('comments_count', 0)} komment: "
                         f"{(p.get('caption') or '').strip()[:200]}")
    except ig.InstagramError as e:
        if e.fatal:
            raise
    hist = ""
    if memory is not None:
        hist = "\n".join(("Egasi: " if h["role"] == "user" else "Jarvis: ") + h["text"][:300]
                         for h in memory.get_recent(6))
    return "\n".join(st), last_post, hist


def apply(res, responder, chat=None):
    """think() natijasini bajaradi. (egasiga matn qatorlari, qayta tekshiriladigan postlar soni yoki None)."""
    lines = [res.get("reply") or "Tushundim."]
    recheck = None
    if res.get("lesson"):
        try:
            add_lesson(res["lesson"])
            lines.append(f"\n📌 Doimiy qoida qilib saqladim: {res['lesson']}\n(ro'yxat: /qoidalar)")
        except ValueError:
            pass
    if res.get("fact"):
        try:
            catalog.add_memory(res["fact"])
            lines.append(f"🧠 Xotiraga yozdim: {res['fact']}")
        except ValueError:
            pass
    for act in res.get("actions") or []:
        t = act["type"]
        if responder is None and t != "dm_mode":
            lines.append("⚠️ Komentga javob tizimi o'chiq, bu sozlamani o'zgartira olmadim.")
            continue
        if t == "set_limit":
            n = responder.set_day_limit(act["value"])
            st = responder.status()
            lines.append(f"✅ Kunlik limit o'rnatildi: {n} (bugun {st['sent_today']}/{st['limit_day']})")
            log_entry(f"Kunlik komment limiti {n} ga o'zgartirildi")
        elif t == "comment_mode":
            responder.set_mode(act["mode"])
            lines.append(f"✅ Komentga javob rejimi: {act['mode']}")
            log_entry(f"Komment rejimi: {act['mode']}")
        elif t == "dm_mode":
            if chat is None:
                lines.append("⚠️ Direkt suhbat tizimi o'chiq.")
                continue
            chat.set_mode(act["mode"])
            lines.append(f"✅ Direkt suhbat rejimi: {act['mode']}")
            log_entry(f"Direkt rejimi: {act['mode']}")
        elif t == "set_policy":
            responder.set_policy(**act["changes"])
            lines.append("✅ Tejash qoidalari yangilandi:\n" + policy_text(responder.status()))
            log_entry("Tejash qoidalari o'zgardi: " + json.dumps(act["changes"], ensure_ascii=False))
        elif t == "recheck_comments":
            recheck = act["posts"]
            lines.append(f"🔎 Oxirgi {recheck} ta postdagi javobsiz komentlarni hozir qayta ko'rib chiqaman, "
                         "tugagach xabar beraman.")
    return lines, recheck
