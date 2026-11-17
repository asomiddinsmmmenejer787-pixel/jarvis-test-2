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
  * reply: egasiga qisqa javob: nimani tushunding, nima qilasan. JURNALga tayanib gapir, to'qima.
    Xatoni tan ol, uzr so'rab cho'zma.

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
        if isinstance(a, dict) and a.get("type") == "recheck_comments":
            try:
                n = int(a.get("posts") or 1)
            except (TypeError, ValueError):
                n = 1
            acts.append({"type": "recheck_comments", "posts": max(1, min(5, n))})
    out["actions"] = acts[:1]
    return out
