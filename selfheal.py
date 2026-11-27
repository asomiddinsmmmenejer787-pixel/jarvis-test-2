"""
selfheal.py
-----------
Jarvis'ning o'z-o'zini tuzatadigan miyasi (27.11 egasi tasdiqladi: uchala qatlam, topilgan qoida
darhol ishlaydi va egasiga xabar boriladi).

1. QOROVUL (har 10 daqiqada, AI ishlatmaydi)
   - komentlar tekshiruvi to'xtab qolganini sezadi;
   - oxirgi postlarni qayta ko'zdan kechirtiradi (o'tib ketgan komment qolmasin);
   - Instagram limiti to'lsa tekshirishni sekinlatadi, tiklangach odatiy tezlikka qaytaradi;
   - yuborish yoki AI xatolari ko'paysa egasini ogohlantiradi.

2. O'Z JAVOBLARINI TEKSHIRISH (soatiga 1 marta, 1 ta AI so'rovi)
   Oxirgi soatda yozilgan komment va direkt javoblarini qayta o'qiydi: robotga o'xshash, takror,
   savoldan qochgan, FAKTLARda yo'q narx yoki va'da... Topilgan har xatodan qisqa qoida chiqadi
   va darhol ishlay boshlaydi (egasining ko'rsatmalari ro'yxatiga "🤖" belgisi bilan qo'shiladi).

3. NATIJADAN O'RGANISH (kuniga 1 marta, kechqurun)
   - mijoz bir savolni qayta so'ragan (birinchi javob yetmagan);
   - direktda Jarvis yozgandan keyin mijoz jim bo'lgan yoki "tushunmadim", "bot" degan;
   - egasi Jarvis javobini o'zgartirib yuborgan yoki direktda o'zi yozgan (eng qimmatli saboq).
   Kunlik hisobot va 3 tagacha yangi qoida.

Jarvis o'z kodini o'zgartirmaydi: faqat qoidalar va ish tezligini. Kod faqat GitHub orqali o'zgaradi.
Har akkaunt (asosiy va o'yinchoq profili) o'z holati va qoidalari bilan ishlaydi.
"""

import json
import logging
import os
import re
import threading
import time
from datetime import datetime

import accounts
import audit
import brain
import catalog
from catalog import TASHKENT

log = logging.getLogger("selfheal")
_lock = threading.Lock()

AUTO_PREFIX = "🤖 "             # Jarvis o'zi topgan qoida belgisi
MAX_AUTO_RULES = 15              # eng ko'p shuncha avto-qoida (eskisi o'chadi)
MAX_RULES_PER_PASS = 3
WATCH_EVERY = 600
REVIEW_EVERY = 3600
DAILY_HOUR = int(os.environ.get("SELFHEAL_DAILY_HOUR", "21") or 21)
STALE_AFTER = 15 * 60            # komentlar shu vaqtdan beri tekshirilmasa - ogohlantirish
SENT_ACTIONS = ("sent",)
NEGATIVE_RE = re.compile(
    r"tushunmad|javob\s*(ber|yoz)ma|bot\s*(mi|misiz|ekan)|robot|nega\s+javob|yana\s+so.?ra|aldad|"
    r"не\s*понял|бот|робот|не\s*ответ", re.I)


# ---------- holat ----------

def _path():
    return accounts.path("selfheal.json")


def load():
    try:
        with open(_path(), encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict):
            d = {}
    except (OSError, ValueError):
        d = {}
    d.setdefault("last_watch", 0)
    d.setdefault("last_review", 0)
    d.setdefault("last_daily", "")
    d.setdefault("last_ok", 0)
    d.setdefault("pause_until", 0)
    d.setdefault("rl_streak", 0)
    d.setdefault("alerts", {})
    d.setdefault("found", [])        # topilgan xatolar: {t, src, problem, rule}
    d.setdefault("reports", [])      # kunlik hisobotlar: {t, text}
    d.setdefault("counts", {"reviews": 0, "mistakes": 0, "rules": 0, "sweeps": 0})
    return d


def save(d):
    d["found"] = d["found"][-60:]
    d["reports"] = d["reports"][-14:]
    try:
        os.makedirs(os.path.dirname(_path()), exist_ok=True)
        with open(_path() + ".tmp", "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(_path() + ".tmp", _path())
    except OSError as e:
        log.warning("Holatni saqlab bo'lmadi: %s", e)


# ---------- 1. qorovul: tezlik va limit ----------

def note_ok():
    """Komentlar tekshiruvi muvaffaqiyatli o'tdi."""
    with _lock:
        d = load()
        d["last_ok"] = time.time()
        if d["rl_streak"]:
            d["rl_streak"] = 0
            d["pause_until"] = 0
        save(d)


def note_rate_limit():
    """Instagram limiti to'ldi: tekshirish 5, 10, 20... daqiqaga (eng ko'pi 60) to'xtatiladi.
    Qaytaradi: necha daqiqa kutiladi."""
    with _lock:
        d = load()
        d["rl_streak"] = min(d["rl_streak"] + 1, 5)
        mins = min(60, 5 * 2 ** (d["rl_streak"] - 1))
        d["pause_until"] = time.time() + mins * 60
        save(d)
    log.warning("Instagram limiti: %s daqiqa sekinlashtirildi (%s)", mins, accounts.current())
    audit.record("selfheal", action="slowdown", minutes=mins)
    return mins


def paused():
    return time.time() < load()["pause_until"]


def _alert_once(d, key, every=6 * 3600):
    now = time.time()
    if now - d["alerts"].get(key, 0) < every:
        return False
    d["alerts"][key] = now
    return True


def watch(responder, now=None, force=False):
    """Qorovul. Qaytaradi: egasiga yuboriladigan xabarlar ro'yxati (ko'pincha bo'sh)."""
    now = now or time.time()
    notes = []
    if responder is None:
        return notes
    with _lock:
        d = load()
        if not force and now - d["last_watch"] < WATCH_EVERY:
            return notes
        d["last_watch"] = now

        # a) tekshiruv to'xtab qoldimi
        if responder.mode != "off" and d["last_ok"] and now - d["last_ok"] > STALE_AFTER:
            mins = int((now - d["last_ok"]) // 60)
            if _alert_once(d, "stale", 2 * 3600):
                notes.append(f"⚠️ Komentlar {mins} daqiqadan beri tekshirilmadi. Qayta urinyapman"
                             + (" (Instagram limiti sababli sekinlashtirilgan)." if now < d["pause_until"] else "."))

        # b) o'tib ketgan komment qolmasin: oxirgi postlar keyingi tekshiruvda to'liq qayta o'qiladi
        try:
            n = 0
            for pid in list(getattr(responder, "_recent_ids", []) or [])[:5]:
                if responder._counts.pop(pid, None) is not None:
                    n += 1
            if n:
                d["counts"]["sweeps"] = d["counts"].get("sweeps", 0) + 1
        except Exception:  # noqa: BLE001
            pass

        # c) xatolar ko'payishi (oxirgi 30 daqiqa)
        acc = accounts.current()
        items = [x for x in audit.recent(400, kinds=("comment",))
                 if x.get("t", 0) >= now - 1800 and x.get("acc", accounts.MAIN) == acc]
        send_err = sum(1 for x in items if x.get("action") == "send_error")
        ai_err = sum(1 for x in items if x.get("action") == "ai_error")
        if send_err >= 3 and _alert_once(d, "send_err"):
            notes.append(f"⚠️ Oxirgi 30 daqiqada {send_err} ta javob Instagram'ga yuborilmadi. "
                         "Token ruxsatlarini yoki Instagram cheklovini tekshiring (/xatolar).")
        if ai_err >= 5 and _alert_once(d, "ai_err"):
            notes.append(f"⚠️ Oxirgi 30 daqiqada AI {ai_err} marta javob bermadi. Zaxira AI ishlayapti; "
                         "takrorlansa /diag bilan tekshiring.")
        save(d)
    for n in notes:
        audit.record("selfheal", action="alert", text=n[:300])
    return notes


# ---------- avto-qoidalar ----------

def _norm(s):
    return set(re.findall(r"[^\W\d_]{3,}", (s or "").lower()))


def _similar(a, b):
    x, y = _norm(a), _norm(b)
    if not x or not y:
        return False
    return len(x & y) / max(1, min(len(x), len(y))) >= 0.7


def auto_rules():
    """[(index_in_lessons, text_without_prefix)]"""
    return [(i, x[len(AUTO_PREFIX):]) for i, x in enumerate(brain.get_lessons()) if x.startswith(AUTO_PREFIX)]


def add_rule(text):
    """Avto-qoida qo'shadi (o'xshashi bo'lsa qo'shmaydi). Qaytaradi: qo'shilgan matn yoki None."""
    text = re.sub(r"\s+", " ", str(text or "")).strip().strip('"«»')[:220]
    if len(text) < 12:
        return None
    if any(_similar(text, x) for x in brain.get_lessons()):
        return None
    brain.add_lesson(AUTO_PREFIX + text)
    autos = auto_rules()
    while len(autos) > MAX_AUTO_RULES:
        brain.delete_lesson(autos[0][0])
        autos = auto_rules()
    return text


def delete_auto(n):
    """n - avto-qoidalar ro'yxatidagi tartib raqami (0 dan)."""
    autos = auto_rules()
    if not 0 <= n < len(autos):
        raise IndexError(n)
    return brain.delete_lesson(autos[n][0])


def _facts_text():
    parts = []
    shop = catalog.load().get("shop", {})
    for k in catalog.SHOP_FIELDS:
        if shop.get(k):
            parts.append(f"Do'kon — {catalog.SHOP_LABELS[k]}: {shop[k]}")
    mem, _ = catalog.memory_block()
    stock, _ = catalog.stock_block()
    parts += [x for x in (mem, stock) if x]
    return "\n".join(parts) or "(egasi faktlar kiritmagan)"


def _apply(found, src, d):
    """AI topgan xatolarni yozadi va qoidalarni qo'shadi. Qaytaradi: qo'shilgan qoidalar."""
    added = []
    now = int(time.time())
    for m in (found or [])[:6]:
        if not isinstance(m, dict):
            continue
        problem = str(m.get("problem") or "").strip()[:300]
        rule = str(m.get("rule") or "").strip()
        if not problem:
            continue
        new = add_rule(rule) if rule and len(added) < MAX_RULES_PER_PASS else None
        d["found"].append({"t": now, "src": src, "problem": problem, "rule": new or "",
                           "example": str(m.get("example") or "")[:200]})
        d["counts"]["mistakes"] = d["counts"].get("mistakes", 0) + 1
        if new:
            added.append(new)
            d["counts"]["rules"] = d["counts"].get("rules", 0) + 1
    return added


# ---------- 2. o'z javoblarini tekshirish ----------

REVIEW_PROMPT = """
Sen bolalar do'koni Instagram sahifasidagi Jarvis (AI sotuvchi) javoblarining qat'iy sifat nazoratchisisan.
Senga oxirgi javoblar beriladi: mijoz nima yozgan va Jarvis nima javob bergan. FAKTLAR va hozirgi QOIDALAR ham bor.
Faqat ANIQ xatolarni top:
- javob robotga o'xshaydi, qolip, sovuq yoki bir xil gap takrorlangan;
- mijoz savoliga javob berilmagan, gap aylantirilgan ("videoda bor" kabi);
- FAKTLARda yo'q narx, chegirma, sovg'a yoki va'da aytilgan; narx FAKTLARdagidan farq qiladi;
- boshqa mahsulot haqida gapirilgan, haddan uzun, noo'rin hazil yoki qo'pol ohang.
Har xato uchun keyingi safar shu xato takrorlanmasligi uchun BITTA aniq qoida yoz: buyruq shaklida,
o'zbek tilida, 160 belgigacha, umumiy gap emas (masalan: "Yetkazib berish so'ralsa 'Toshkent bo'ylab 1 kunda'
deb aniq ayt, direktga yo'llama"). Hozirgi QOIDALARda borini takrorlama. Ishonching komil bo'lmasa xato deb yozma.
Xato bo'lmasa bo'sh ro'yxat qaytar.
JSON: {"mistakes":[{"n":1,"problem":"qisqa tavsif","example":"Jarvis javobidan parcha","rule":"..."}]}
""".strip()


def _recent_replies(since, limit=25):
    acc = accounts.current()
    out = []
    for x in audit.recent(1500, kinds=("comment", "dm")):
        if x.get("t", 0) < since or x.get("acc", accounts.MAIN) != acc:
            continue
        if x.get("k") == "comment" and x.get("action") in SENT_ACTIONS and x.get("reply"):
            out.append(f"KOMMENT ({x.get('category', '')}) @{x.get('user', '')}: «{x.get('text', '')}»\n"
                       f"JARVIS: «{x.get('reply')}»")
        elif x.get("k") == "dm" and x.get("type") in ("dm_sent", "chat_sent") and x.get("text"):
            asked = x.get("message") or x.get("comment") or ""
            out.append(f"DIREKT @{x.get('username', '')}: «{asked}»\nJARVIS: «{x.get('text')}»")
    return out[-limit:]


def review(ai, now=None, force=False):
    """Soatlik o'z-o'zini tekshirish. Qaytaradi: dict(checked, mistakes, rules) yoki None (vaqti emas)."""
    now = now or time.time()
    with _lock:
        d = load()
        if not force and now - d["last_review"] < REVIEW_EVERY:
            return None
        since = (now - REVIEW_EVERY) if force else (d["last_review"] or now - REVIEW_EVERY)
        d["last_review"] = now
        save(d)
    items = _recent_replies(since)
    if not items:
        return {"checked": 0, "mistakes": [], "rules": []}
    lessons = "\n".join(f"- {x}" for x in brain.get_lessons()) or "(yo'q)"
    msg = (f"FAKTLAR:\n{_facts_text()}\n\nHOZIRGI QOIDALAR:\n{lessons}\n\nOXIRGI JAVOBLAR:\n"
           + "\n\n".join(f"{i}. {t}" for i, t in enumerate(items, 1)))
    res = ai.generate_json(REVIEW_PROMPT, msg[:14000], temperature=0.2)
    found = (res or {}).get("mistakes") if isinstance(res, dict) else None
    with _lock:
        d = load()
        d["counts"]["reviews"] = d["counts"].get("reviews", 0) + 1
        rules = _apply(found if isinstance(found, list) else [], "review", d)
        save(d)
    out = {"checked": len(items), "mistakes": [m for m in (found or []) if isinstance(m, dict)], "rules": rules}
    audit.record("selfheal", action="review", checked=out["checked"], mistakes=len(out["mistakes"]), rules=rules)
    return out


def review_text(res):
    if not res or not res.get("mistakes"):
        return None
    lines = [f"🧠 O'zimni tekshirdim: {res['checked']} ta javobdan {len(res['mistakes'])} tasida xato topdim."]
    for m in res["mistakes"][:4]:
        lines.append(f"• {m.get('problem', '')}")
    if res["rules"]:
        lines.append("\nYangi qoidalar (darhol ishlayapti):")
        lines += [f"🤖 {r}" for r in res["rules"]]
        lines.append("\nNoto'g'risi bo'lsa: /qoidalar → /qoidaochir <raqam> yoki HUD'da o'chiring.")
    return "\n".join(lines)


# ---------- 3. natijadan o'rganish ----------

DAILY_PROMPT = """
Sen bolalar do'koni Instagram sahifasidagi Jarvis (AI sotuvchi) ishining kunlik tahlilchisisan.
Senga bugungi NATIJALAR beriladi: egasi Jarvis javobini qanday tuzatgani, mijoz qayta so'ragan savollar,
direktda mijoz Jarvisdan keyin jim bo'lgan yoki norozi bo'lgan yozishmalar, va yaxshi davom etganlari.
Egasining tuzatishlari eng muhim saboq: u qanday yozsa, Jarvis ham shunday yozishi kerak.
Vazifa: nima ishladi, nima ishlamadi; va 3 tagacha aniq, qisqa qoida (buyruq shaklida, o'zbekcha, 160 belgigacha).
Hozirgi QOIDALARda borini takrorlama. Dalil bo'lmasa qoida yozma.
JSON: {"worked":["..."],"failed":["..."],"mistakes":[{"problem":"...","example":"...","rule":"..."}]}
""".strip()


def _outcomes(chat, since):
    acc = accounts.current()
    edits, repeats, sent = [], [], {}
    for x in audit.recent(3000, kinds=("comment", "owner_edit")):
        if x.get("t", 0) < since or x.get("acc", accounts.MAIN) != acc:
            continue
        if x.get("k") == "owner_edit":
            edits.append(f"Mijoz: «{x.get('customer', '')}»\nJarvis taklifi: «{x.get('draft', '')}»\n"
                         f"Egasi yubordi: «{x.get('owner', '')}»")
        elif x.get("action") in SENT_ACTIONS and x.get("category") in ("price", "question"):
            key = (x.get("user"), x.get("post"))
            if key in sent:
                repeats.append(f"@{x.get('user')}: avval «{sent[key][0]}» → Jarvis «{sent[key][1]}», "
                               f"keyin yana: «{x.get('text', '')}»")
            sent[key] = (x.get("text", ""), x.get("reply", ""))
    silent, unhappy, good, owner_took = [], [], 0, []
    mem = getattr(chat, "memory", None)
    convs = list((getattr(mem, "d", {}) or {}).values()) if mem else []
    for conv in convs:
        msgs = [m for m in conv.get("msgs", []) if m.get("t", 0) >= since]
        if not msgs:
            continue
        who = [m.get("who") for m in msgs]
        user = conv.get("username") or "?"
        text = "\n".join(f"{m.get('who')}: {m.get('text', '')[:200]}" for m in msgs[-6:])
        if any(m.get("who") == "mijoz" and NEGATIVE_RE.search(m.get("text", "")) for m in msgs):
            unhappy.append(f"@{user}\n{text}")
        elif "egasi" in who and "jarvis" in who:
            owner_took.append(f"@{user}\n{text}")
        elif who and who[-1] == "jarvis" and time.time() - msgs[-1].get("t", 0) > 3 * 3600:
            silent.append(f"@{user}\n{text}")
        elif "jarvis" in who and who[-1] == "mijoz":
            good += 1
    return {"edits": edits, "repeats": repeats, "silent": silent, "unhappy": unhappy,
            "owner_took": owner_took, "good": good}


def daily(ai, chat, now=None, force=False):
    """Kunlik o'rganish (DAILY_HOUR dan keyin, kuniga bir marta). Qaytaradi: hisobot matni yoki None."""
    now = now or time.time()
    dt = datetime.fromtimestamp(now, TASHKENT)
    today = dt.strftime("%Y-%m-%d")
    with _lock:
        d = load()
        if not force and (d["last_daily"] == today or dt.hour < DAILY_HOUR):
            return None
        d["last_daily"] = today
        save(d)
    o = _outcomes(chat, now - 86400)
    n_signals = sum(len(o[k]) for k in ("edits", "repeats", "silent", "unhappy", "owner_took"))
    res = {}
    rules = []
    if n_signals:
        def block(title, xs, k=6):
            return f"{title} ({len(xs)}):\n" + ("\n---\n".join(xs[:k]) if xs else "(yo'q)")
        lessons = "\n".join(f"- {x}" for x in brain.get_lessons()) or "(yo'q)"
        msg = "\n\n".join([
            f"FAKTLAR:\n{_facts_text()}", f"HOZIRGI QOIDALAR:\n{lessons}",
            block("EGASI JAVOBNI TUZATDI", o["edits"]), block("MIJOZ QAYTA SO'RADI", o["repeats"]),
            block("DIREKTDA EGASI O'ZI YOZDI", o["owner_took"]), block("MIJOZ NOROZI", o["unhappy"]),
            block("JARVISDAN KEYIN JIM BO'LDI", o["silent"], 4),
            f"YAXSHI DAVOM ETGAN YOZISHMALAR: {o['good']} ta"])
        r = ai.generate_json(DAILY_PROMPT, msg[:16000], temperature=0.2)
        res = r if isinstance(r, dict) else {}
        with _lock:
            d = load()
            rules = _apply(res.get("mistakes") if isinstance(res.get("mistakes"), list) else [], "daily", d)
            save(d)
    with _lock:
        d = load()
        day = [x for x in d["found"] if x.get("t", 0) >= now - 86400]
    lines = [f"🌙 Kunlik o'z-o'zini tahlil{(' · ' + accounts.label()) if accounts.label() else ''}",
             f"Bugun o'zimda {len(day)} ta xato topdim, {sum(1 for x in day if x.get('rule'))} ta yangi qoida chiqardim."]
    stat = []
    if o["edits"]:
        stat.append(f"siz {len(o['edits'])} ta javobimni tuzatdingiz")
    if o["repeats"]:
        stat.append(f"{len(o['repeats'])} mijoz savolini qayta so'radi")
    if o["silent"]:
        stat.append(f"{len(o['silent'])} mijoz direktda jim bo'lib qoldi")
    if o["unhappy"]:
        stat.append(f"{len(o['unhappy'])} mijoz norozi bo'ldi")
    if o["good"]:
        stat.append(f"{o['good']} ta yozishma yaxshi davom etdi")
    if stat:
        lines.append("Natija: " + ", ".join(stat) + ".")
    for k, title in (("worked", "✅ Ishladi"), ("failed", "❌ Ishlamadi")):
        xs = [str(x) for x in (res.get(k) or []) if x][:3]
        if xs:
            lines.append(f"\n{title}:\n" + "\n".join(f"• {x}" for x in xs))
    if rules:
        lines.append("\nYangi qoidalar (darhol ishlayapti):\n" + "\n".join(f"🤖 {r}" for r in rules))
    text = "\n".join(lines)
    with _lock:
        d = load()
        d["reports"].append({"t": int(now), "text": text})
        save(d)
    audit.record("selfheal", action="daily", signals=n_signals, rules=rules)
    return text


# ---------- HUD uchun ----------

def view():
    d = load()
    return {
        "last_review": d["last_review"], "last_daily": d["last_daily"], "last_ok": d["last_ok"],
        "paused_until": d["pause_until"] if d["pause_until"] > time.time() else 0,
        "counts": d["counts"], "found": d["found"][-20:][::-1],
        "rules": [{"i": n, "text": t} for n, (_, t) in enumerate(auto_rules())],
        "report": (d["reports"][-1] if d["reports"] else None),
    }
