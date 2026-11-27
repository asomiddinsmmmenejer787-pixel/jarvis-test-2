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
import re
import logging
import os
import threading
import time
from datetime import datetime

import catalog
import accounts
import config
import instagram_api as ig

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
    return accounts.path("journal.json")


def _jload():
    try:
        with open(_jpath(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


def log_entry(text):
    try:
        import audit
        audit.record("journal", x=str(text)[:300])
    except Exception:  # noqa: BLE001
        pass
    with _lock:
        items = _jload()
        items.append({"t": int(time.time()), "x": str(text)[:300]})
        items = items[-MAX_JOURNAL:]
        try:
            os.makedirs(os.path.dirname(_jpath()), exist_ok=True)
            with open(_jpath() + ".tmp", "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False)
            os.replace(_jpath() + ".tmp", _jpath())
        except OSError as e:
            log.warning("Jurnalni saqlab bo'lmadi: %s", e)


def log_event(ev):
    """auto_reply / dm_chat voqeasini jurnalga qisqa yozadi."""
    t = ev.get("type")
    if t in ("dm_sent", "dm_error", "dm_approval", "chat_sent", "chat_approval", "chat_error"):
        import audit
        audit.record("dm", **{k: v for k, v in ev.items() if k in (
            "type", "cid", "key", "username", "comment", "message", "history", "text", "kind", "reason",
            "error", "topic", "safe")})
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


def journal_items(n=20):
    """Oxirgi ishlar (yangisi birinchi): HUD'dagi jonli lenta uchun."""
    return [{"t": it.get("t", 0), "x": it.get("x", "")} for it in reversed(_jload()[-n:])]


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
bergan qoidalar), POSTLAR, SUHBAT (oxirgi gaplashuv) va EGASINING XABARI.

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
    {"type": "post_reply", "post": 2, "on": false}  - bitta postga komment javobini o'chirish/yoqish.
       "post" = OXIRGI POSTLAR ro'yxatidagi raqam (1 = eng yangi). Egasi "shu videoga yozma",
       "blogerlar videosiga koment yozishni to'xtat" desa, ro'yxatdan mos postni tanla. Aniq bo'lmasa,
       harakat qilma, reply'da qaysi post ekanini so'ra.
    {"type": "post_dm", "post": 2, "on": false}  - bitta post komentlaridan keyin direktga yozishni
       o'chirish/yoqish (komentga javob davom etadi). Egasi "shu postga direktga yozma",
       "bu videoga lichkaga yozma" desa.
    {"type": "post_task", "post": 2, "task": "..."}  - FAQAT shu post komentlariga qanday javob yozish
       bo'yicha egasining ko'rsatmasi. Egasi "shu postga/videoga komentga shunday javob ber", "bu postda
       ... deb yoz", "tanlov postida ... eslat" desa, task'ga uning ko'rsatmasini aniq, o'z so'zlari bilan
       yoz (masalan "Komentga: g'olib 1-dekabr kuni aniqlanadi deb javob ber"). Bu holatda lesson BO'SH
       qoladi (lesson hamma postlarga tegishli). "task": "" = shu postdagi ko'rsatmani o'chirish.
       Postda oldingi ko'rsatma bo'lsa va egasi uni bekor qilmagan bo'lsa, ikkalasini bitta task'ga birlashtir.
    POST QAYSI: "post" = OXIRGI POSTLAR ro'yxatidagi raqam (1 = eng yangi). "EGASI KO'RSATGAN POST" bo'lsa,
       aynan o'shani ishlat. Egasi "oxirgi post", "tanlov posti", "krasovka videosi" desa, ro'yxatdagi
       sana va opisaniyaga qarab tanla. Umuman aniq bo'lmasa, harakat qilma, reply'da qaysi post ekanini
       so'ra (havolasini yuborishni so'ra).
    {"type": "set_policy", ...}  - limitni tejash qoidalari, faqat o'zgarayotgan kalitlarni yoz:
       "saving": true/false - limitni tejash umuman yoqilgan/o'chirilgan ("limit tejama" -> false).
       Quyidagi cheklovlar faqat saving=true bo'lsa ishlaydi ("skip" doim ishlaydi):
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


_LINK_RE = re.compile(r"instagram\.com/(?:[\w.]+/)?(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)")


def find_linked_post(text):
    """Egasi yuborgan havola bizning so'nggi postlarimizdan biriga tegishli bo'lsa, o'sha post."""
    codes = set(_LINK_RE.findall(text or ""))
    if not codes:
        return None
    try:
        posts = ig.get_recent_posts(50)
    except ig.InstagramError as e:
        if e.fatal:
            raise
        return None
    for p in posts:
        m = _LINK_RE.search(p.get("permalink") or "")
        if m and m.group(1) in codes:
            return p
    return None


def think(ai, owner_text, status_text="", last_post_text="", history_text=""):
    lessons = get_lessons()
    linked = find_linked_post(owner_text)
    hint = ""
    if linked is not None:
        cap = " ".join((linked.get("caption") or "").split())[:110]
        hint = (f"\n\nEGASI KO'RSATGAN POST (havola orqali): {catalog._post_time(linked)}: {cap}\n"
                "post_* harakatlarida \"post\": 1 yoz, tizim aynan shu postni ishlatadi.")
    msg = (f"HOLAT:\n{status_text or '-'}\n\nJURNAL:\n{journal_text(30) or '-'}\n\n"
           f"KO'RSATMALAR:\n" + ("\n".join(f"- {x}" for x in lessons) or "-")
           + f"\n\nPOSTLAR:\n{last_post_text or '-'}{hint}\n\nSUHBAT:\n{history_text or '-'}"
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
            elif t in ("post_reply", "post_dm"):
                acts.append({"type": t, "post": max(1, min(15, int(a.get("post") or 1))), "on": bool(a.get("on"))})
            elif t == "post_task" and isinstance(a.get("task"), str):
                acts.append({"type": t, "post": max(1, min(15, int(a.get("post") or 1))),
                             "task": catalog._clean(a["task"], 500)})
            elif t == "set_policy":
                ch = {k: int(a[k]) for k in ("low_limit", "per_user_day", "short_ratio", "reserve_pct")
                      if a.get(k) is not None and str(a[k]).lstrip("-").isdigit()}
                if isinstance(a.get("skip"), list):
                    ch["skip"] = [str(x) for x in a["skip"]]
                if isinstance(a.get("saving"), bool):
                    ch["saving"] = a["saving"]
                if ch:
                    acts.append({"type": t, "changes": ch})
        except (TypeError, ValueError):
            continue
    if linked is not None:
        for a in acts:
            if a["type"].startswith("post_"):
                a["media"] = linked  # havola berilgan bo'lsa, raqamga emas, aynan shu postga
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
    if not p.get("saving"):
        return (f"Kunlik limit: {st['sent_today']}/{lim}\n"
                f"Bugun: {used}\n"
                "Limitni tejash O'CHIQ: hamma komentlarga javob yoziladi\n"
                f"Umuman javob yozilmaydi: {skip}")
    return (f"Kunlik limit: {st['sent_today']}/{lim}\n"
            f"Bugun: {used}\n"
            "Limitni tejash YONIQ\n"
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
    stock_text, _ = catalog.stock_block()
    if stock_text:
        st.append(stock_text)
    last_post = ""
    try:
        posts = ig.get_recent_posts(10)
        if posts:
            rows = []
            for i, p in enumerate(posts, 1):
                cap = " ".join((p.get("caption") or "").split())[:110]
                off = " [KOMMENTGA JAVOB O'CHIQ]" if catalog.is_reply_off(p.get("id")) else ""
                off += " [DIREKTGA YOZILMAYDI]" if catalog.is_dm_off(p.get("id")) else ""
                task = catalog.get_post_info(p.get("id"))["task"]
                off += f" [SHU POST KO'RSATMASI: {task[:150]}]" if task else ""
                rows.append(f"{i}. {catalog._post_time(p)}, {p.get('comments_count', 0)} komment{off}: {cap}")
            last_post = "OXIRGI POSTLAR (1 = eng yangi):\n" + "\n".join(rows)
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
        elif t.startswith("post_"):
            p = act.get("media")
            if p is None:
                posts = ig.get_recent_posts(15)
                if act["post"] > len(posts):
                    lines.append("⚠️ Bunday post topilmadi. Post havolasini yuboring.")
                    continue
                p = posts[act["post"] - 1]
            cap = " ".join((p.get("caption") or "").split())[:60]
            where = f"{catalog._post_time(p)} «{cap}» {p.get('permalink') or ''}"
            if t == "post_reply":
                catalog.set_reply_off(p["id"], not act["on"])
                lines.append(("✅ Komentlarga javob yoqildi: " if act["on"] else "⛔ Bu postga komment yozilmaydi: ")
                             + where)
                log_entry(f"Post {p['id']} komment javobi: {'yoqildi' if act['on'] else 'ochirildi'}")
            elif t == "post_dm":
                catalog.set_dm_off(p["id"], not act["on"])
                lines.append(("✅ Bu post komentlaridan keyin direktga yana yoziladi: " if act["on"]
                              else "⛔ Bu postga faqat komentda javob, direktga yozilmaydi: ") + where)
                log_entry(f"Post {p['id']} direkt: {'yoqildi' if act['on'] else 'ochirildi'}")
            elif t == "post_task":
                catalog.set_task(p["id"], act["task"])
                lines.append((f"📝 Shu post komentlariga endi shunday javob beraman: {act['task']}\n"
                              if act["task"] else "🗑 Shu postdagi alohida ko'rsatma o'chirildi: ") + where)
                log_entry(f"Post {p['id']} ko'rsatmasi: {act['task'][:150] or 'ochirildi'}")
        elif t == "set_policy":
            responder.set_policy(**act["changes"])
            lines.append("✅ Tejash qoidalari yangilandi:\n" + policy_text(responder.status()))
            log_entry("Tejash qoidalari o'zgardi: " + json.dumps(act["changes"], ensure_ascii=False))
        elif t == "recheck_comments":
            recheck = act["posts"]
            lines.append(f"🔎 Oxirgi {recheck} ta postdagi javobsiz komentlarni hozir qayta ko'rib chiqaman, "
                         "tugagach xabar beraman.")
    return lines, recheck
