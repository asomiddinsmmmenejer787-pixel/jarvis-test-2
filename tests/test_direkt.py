"""
tests/test_direkt.py
--------------------
Post asosidagi javoblar va komentdan keyin direktga xabar (private reply) testlari.

Ishga tushirish:
    python -m tests.test_direkt
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="jarvis-dm-")
os.environ["INSTAGRAM_ACCESS_TOKEN"] = "ENV_TOKEN_1"
os.environ["REPLY_DELAY_MIN"] = os.environ["REPLY_DELAY_MAX"] = "0"

import auto_reply  # noqa: E402
import catalog  # noqa: E402
import instagram_api as ig  # noqa: E402
from tests.test_instagram import FakeAI, FakeInstagram, check, ts  # noqa: E402


class DmAI(FakeAI):
    """Komentga javob + direkt matni. Oxirgi so'rovlar eslab qolinadi."""

    def __init__(self, dm=None):
        super().__init__()
        self.dm = dm
        self.last_dm_msg = None
        self.last_reply_msg = None

    def generate_json(self, system, message, temperature=0.9):
        if system.startswith("Sen do'kon nomidan Instagram direktda"):
            self.last_dm_msg = message
            return self.dm
        self.last_reply_msg = message
        self.last_system = system
        res = super().generate_json(system, message, temperature)
        if self.dm and res.get("category") in ("price", "question"):  # endi direkt matni shu javob ichida keladi
            res.update(dm=self.dm.get("text", ""), dm_answered=self.dm.get("answered", False),
                       topic=self.dm.get("topic", ""))
        return res


def run():
    os.environ["AUTO_REPLY_MODE"] = "on"  # test_instagram importi "test" qilib qo'yadi
    fake = FakeInstagram()
    ig.requests.request = fake.request

    print("\n1) Post ma'lumotlari")
    catalog.set_shop({"address": "Toshkent, Chilonzor 9", "phone": "+998 77 499 47 44", "delivery": ""})
    check(catalog.load()["shop"] == {"address": "Toshkent, Chilonzor 9", "phone": "+998 77 499 47 44"},
          "Do'kon ma'lumotlari saqlandi (bo'sh maydon saqlanmadi)")
    post = {"id": "m1", "caption": "Samokat 3-8 yosh uchun\nNarxi: 1 250 000 so'm", "timestamp": ts(5)}
    f = catalog.post_facts(post)
    check(f["has_price"] and any("opisaniyasi" in line for line in f["lines"]), "Opisaniyadagi narx tanildi")
    check(any("Post joylangan vaqt" in line for line in f["lines"]), "Post sanasi va vaqti faktlarda bor")
    catalog.set_post_info("m1", {"title": "Samokat S10", "price": "1 190 000 so'm", "info": "Kafolat 6 oy"})
    f = catalog.post_facts(post)
    check(any("egasi kiritgan, eng ishonchli" in line and "1 190 000" in line for line in f["lines"]),
          "Qo'lda kiritilgan narx ustun belgilangan")
    catalog.set_post_info("m1", {"title": "Samokat S10", "price": "1 190 000 so'm", "use_caption": False})
    check(not any("opisaniyasi" in line for line in catalog.post_facts(post)["lines"]), "Opisaniyani o'chirish ishlaydi")
    catalog.set_post_info("m1", {"use_caption": True})
    check("m1" not in catalog.load()["posts"], "Bo'sh ma'lumot o'chirildi")
    check(not catalog.post_facts({"id": "x", "caption": "Yangi kolleksiya"})["has_price"], "Narxsiz post tanildi")

    print("\n2) Raqamlar tekshiruvi")
    src = "Narxi: 1 250 000 so'm\nTelefon: +998 77 499 47 44"
    check(catalog.numbers_ok("Narxi 1 250 000 so'm, tel 77 499 47 44", src), "To'g'ri narx va telefon o'tdi")
    check(catalog.numbers_ok("1250 ming so'm", src), "'1250 ming' yozuvi tanildi")
    check(not catalog.numbers_ok("Narxi 990 000 so'm", src), "To'qilgan narx ushlandi")

    good = {"text": "Salom! Samokat narxi 1 250 000 so'm. Buyurtma: +998 77 499 47 44",
            "answered": True, "topic": "samokat narxi"}
    r = catalog.compose_dm(DmAI(dm=good), post, "narxi qancha?")
    check(r["safe"] and r["topic"] == "samokat narxi", "To'g'ri javob xavfsiz deb belgilandi")
    r = catalog.compose_dm(DmAI(dm={**good, "text": "Narxi 990 000 so'm"}), post, "narxi?")
    check(not r["safe"] and "raqam" in r["reason"], "To'qilgan narxli xabar xavfli deb belgilandi")
    r = catalog.compose_dm(DmAI(dm={"text": "Aniqlab yozamiz", "answered": False}), post, "rangi bormi?")
    check(not r["safe"] and "javob yo'q" in r["reason"], "Postda javob bo'lmasa xavfsiz emas")
    ai = DmAI(dm=good)
    catalog.compose_dm(ai, post, "Narx?")
    check("Narxi: 1 250 000" in ai.last_dm_msg and "Chilonzor" in ai.last_dm_msg
          and "<komment>Narx?</komment>" in ai.last_dm_msg,
          "AI'ga post opisaniyasi, do'kon ma'lumoti va komment uzatildi")

    print("\n3) Komentdan keyin direkt — TASDIQ rejimi")
    ai = DmAI(dm={"text": "Salom! Narxini aniqlab yozamiz.", "answered": False, "topic": "narx"})
    r = auto_reply.CommentResponder(ai)
    check(r.dm_mode == "auto", "Standart direkt rejimi: avto")
    r.set_dm_mode("approve")
    events = r.run_once()
    types = [(e["type"], e["cid"]) for e in events]
    check(("sent", "c1") in types and ("dm_approval", "c1") in types, "Narx so'ragan komentga direkt tayyorlandi")
    check("FAKTLAR" in ai.last_reply_msg, "Ochiq javobga ham post faktlari uzatildi")
    check(not fake.dms, "Tasdiqsiz direkt yuborilmadi")
    dm = next(d for d in r.get_dm_pending() if d["cid"] == "c1")
    check(dm["post_time"] and dm["caption"].startswith("Bolangiz") and dm["reason"],
          "Direktda post vaqti va sabab ko'rinadi")
    r.set_dm_message_id("c1", 42)
    check(r.find_dm_by_message(42) == "c1", "Telegram xabari direktga bog'landi")
    msg = r.approve_dm("c1", "Salom! Narxi 1 190 000 so'm.")
    check(msg.startswith("📩") and fake.dms[-1] == ("c1", "Salom! Narxi 1 190 000 so'm."), "Egasining matni yuborildi")
    check("allaqachon" in r.approve_dm("c1"), "Ikki marta yuborilmaydi")

    print("\n4) AVTO rejim")
    r.set_dm_mode("auto")
    catalog.set_post_info("m1", {"title": "Samokat", "price": "1 250 000 so'm"})
    ai.dm = {"text": "Salom! Samokat narxi 1 250 000 so'm.", "answered": True, "topic": "narx"}
    fake.comments["m1"].append({"id": "c20", "text": "Narx qancha bo'ladi?", "username": "u20", "timestamp": ts(1)})
    events = r.run_once()
    check(any(e["type"] == "dm_sent" and e["cid"] == "c20" for e in events) and fake.dms[-1][0] == "c20",
          "Javob postda aniq bo'lsa darhol yuborildi")
    ai.dm = {"text": "Narxi 999 000 so'm", "answered": True, "topic": "narx"}
    fake.comments["m1"].append({"id": "c21", "text": "Narxi qancha", "username": "u21", "timestamp": ts(1)})
    events = r.run_once()
    sent = next(e for e in events if e["type"] == "dm_sent" and e["cid"] == "c21")
    check("999" not in fake.dms[-1][1] and fake.dms[-1] == ("c21", catalog.fallback_dm()) and sent["reason"],
          "To'qilgan narx yuborilmadi: o'rniga narxsiz xabar ketdi va egasi ogohlantirildi")
    catalog.add_memory("Rolikli krossovka — 389 000 so'm, 31-38 o'lcham")
    ai.dm = {"text": "Rolikli krossovka 389 000 so'm.", "answered": True, "topic": "narx"}
    fake.comments["m1"].append({"id": "c25", "text": "Narxi nechchi?", "username": "u25", "timestamp": ts(1)})
    events = r.run_once()
    check(any(e["type"] == "dm_sent" and e["cid"] == "c25" and not e["reason"] for e in events)
          and "DO'KON XOTIRASI" in ai.last_reply_msg, "Xotiradagi narx AI'ga uzatildi va tekshiruvdan o'tdi")
    check(catalog.delete_memory(0).startswith("Rolikli") and not catalog.get_memory(), "Xotiradan o'chirish ishlaydi")
    print("\n4b) Egasi: oxirgi ikkita postga direktga yozilmasin, faqat komentga javob")
    done = r.apply_owner_setup()
    check([p["id"] for p in done] == ["m1", "m2"] and catalog.is_dm_off("m1") and catalog.is_dm_off("m2")
          and not catalog.is_dm_off("m3"), "Oxirgi ikkita post uchun direkt o'chirildi")
    check(r.apply_owner_setup() == [], "Bu sozlama faqat bir marta qo'llanadi (keyingi postlarga tegmaydi)")
    ai.dm = {"text": "Samokat narxi 1 250 000 so'm.", "answered": True, "topic": "narx"}
    fake.comments["m1"].append({"id": "c26", "text": "Narxi qancha?", "username": "u26", "timestamp": ts(1)})
    n = len(fake.dms)
    events = r.run_once()
    rep = [t for c, t in fake.replies if c == "c26"]
    check(len(fake.dms) == n and not any(e["type"].startswith("dm") for e in events),
          "Direkt o'chirilgan postdagi narx komentiga direkt yuborilmadi")
    check(rep and "direktga" in rep[0].lower() and not auto_reply.DM_CLAIM_RE.search(rep[0]),
          "Komentga javob yozildi: 'direktga yozdik' emas, direktga yozishga taklif")
    check("DIREKT O'CHIQ" in ai.last_system and "yozdik" in ai.last_system, "AI'ga 'direktga yozdik dema' deyildi")
    check(auto_reply.DM_CLAIM_RE.search("Narxini direktga yozdik 😊")
          and auto_reply.DM_CLAIM_RE.search("Direktingizga yuboramiz")
          and not auto_reply.DM_CLAIM_RE.search("Narxini direktga yozing, shaxsan aytamiz"),
          "'Direktga yozdik' degan yolg'on javob aniqlanadi")
    catalog.set_dm_off("m1", False)
    catalog.set_dm_off("m2", False)

    print("\n4b2) Model nomi qisqa maqtov emas; narxsiz direktda 'narxini yozdik' deyilmaydi")
    check(not auto_reply.is_short("Kukirin g2") and not auto_reply.is_short("G2")
          and auto_reply.is_short("😍😍😍") and auto_reply.is_short("qiz2 zo'r"),
          "'Kukirin g2' kabi model nomi tejashga tushmaydi, emoji esa tushadi")
    check(auto_reply.dm_will_fallback("", "300 000 so'm") and auto_reply.dm_will_fallback("Narxi 450 000", "300 000")
          and not auto_reply.dm_will_fallback("Narxi 300 000 so'm", "300 000 so'm"),
          "Direktga umumiy xabar ketishi oldindan aniqlanadi")
    fixed = auto_reply.honest_price_claim("Narxi va batafsil ma'lumotni direktga yozdik 😊")
    check(fixed in auto_reply.DM_ASK_REPLIES and "narx" not in fixed.lower(),
          "Narxsiz direktda komentda 'narxini yozdik' yolg'oni yo'q")
    check(auto_reply.honest_price_claim("Direktga qarang 😊") == "Direktga qarang 😊",
          "Narx haqida da'vosiz javob o'zgarmaydi")

    print("\n4b3) So'rovnoma posti: 'qaysi tovar kerak?' ostidagi mahsulot nomi narx so'rovi emas")
    check(catalog.SURVEY_RE.search("Katta skidka oldidan qaysi tovarlar kerak? Kommentga yozing")
          and catalog.SURVEY_RE.search("Какие товары вам нужны? Напишите в комментариях")
          and not catalog.SURVEY_RE.search("Samokat 3-8 yosh uchun. Direktga yozing"),
          "So'rovnoma posti tanildi, oddiy reklama posti emas")
    sp = {"id": "s1", "caption": "Katta skidka oldidan qaysi tovarlar kerak? Komentga yozing"}
    check(catalog.post_facts(sp)["survey"] and any("SO'ROVNOMA" in x for x in catalog.post_facts(sp)["lines"]),
          "AI'ga 'bu post so'rovnoma' deb aytiladi")
    v = auto_reply.survey_answer({"category": "price", "reply": "Narxini direktga yozdik 😊", "action": "auto"},
                                 "Kukirin g2", True)
    check(v["category"] == "positive" and v["reply"] in auto_reply.SURVEY_REPLIES,
          "'Kukirin g2' istak deb olinadi: narx va direkt haqida gap yo'q")
    check(auto_reply.survey_answer({"category": "price", "reply": "x", "action": "auto"}, "Kukirin narxi qancha?",
                                   True)["category"] == "price", "Aniq narx so'ralsa, narx so'rovi bo'lib qoladi")
    check(auto_reply.survey_answer({"category": "price", "reply": "x"}, "Kukirin g2", False)["category"] == "price",
          "Oddiy postda o'zgarmaydi")
    catalog.set_task("m1", "Bu so'rovnoma: katta skidka oldidan qaysi tovarlar kerakligini so'radik")
    orig = ai.generate_json

    def kukirin_ai(system, message, temperature=0.9):
        if "<komment>Kukirin g2</komment>" in message:
            check("SO'ROVNOMA" in message and "SO'ROVNOMA POSTI" in system, "AI so'rovnoma haqida biladi")
            return {"category": "price", "confidence": 0.9, "reply": "Narxi va ma'lumotni direktga yozdik 😊",
                    "dm": "Kukirin G2 narxi 300 000", "dm_answered": True}
        return orig(system, message, temperature)
    ai.generate_json = kukirin_ai
    dms_before = len(fake.dms)
    fake.comments["m1"].append({"id": "c29k", "text": "Kukirin g2", "username": "u29k", "timestamp": ts(1)})
    events = r.run_once()
    sent = next(e for e in events if e["type"] == "sent" and e["cid"] == "c29k")
    check(sent["category"] == "positive" and sent["reply"] in auto_reply.SURVEY_REPLIES
          and len(fake.dms) == dms_before, "So'rovnomadagi 'Kukirin g2' ga: 'yozib oldik', direktga narx ketmadi")
    ai.generate_json = orig
    catalog.set_task("m1", "")

    print("\n4c) Egasi: 'Krasofka haqidagi komentlarga faqat komentda javob, direktga o'tkazma'")
    import brain
    lesson = "Krasofka haqidagi komentlarga faqat kommentda javob ber, direktga o'tkazma."
    check(auto_reply.lesson_blocks_dm("Kirasofka", [lesson]) and auto_reply.lesson_blocks_dm("Krasovka 😍", [lesson])
          and not auto_reply.lesson_blocks_dm("Samokat narxi?", [lesson])
          and not auto_reply.lesson_blocks_dm("Krasofka", ["Krasofka narxi 300 000 so'm"])
          and not auto_reply.lesson_blocks_dm("Krasofka", ["19-noyabr oxirgi postga direktga yozishni to'xtat"]),
          "Ko'rsatma faqat aytilgan mahsulotga va faqat direkt taqiqi bo'lsa qo'llanadi")
    brain.add_lesson(lesson)
    ai.dm = {"text": "Krasofka narxi 300 000 so'm.", "answered": True, "topic": "narx"}
    fake.comments["m1"].append({"id": "c27", "text": "Krasofka narxi qancha?", "username": "u27", "timestamp": ts(1)})
    n = len(fake.dms)
    events = r.run_once()
    rep = [t for c, t in fake.replies if c == "c27"]
    check(len(fake.dms) == n and not any(e["type"].startswith("dm") for e in events),
          "Krasofka komentiga direkt yuborilmadi (egasining ko'rsatmasi)")
    check(rep and not auto_reply.DM_CLAIM_RE.search(rep[0]), "Komentda 'direktga yozdik' degan yolg'on yo'q")
    ai.dm = {"text": "Samokat narxi 1 250 000 so'm.", "answered": True, "topic": "narx"}
    fake.comments["m1"].append({"id": "c28", "text": "Samokat narxi qancha?", "username": "u28", "timestamp": ts(1)})
    events = r.run_once()
    check(any(e["type"] == "dm_sent" and e["cid"] == "c28" for e in events), "Boshqa mahsulotga direkt yozilaveradi")
    catalog.save({**catalog.load(), "lessons": []})

    fake.dm_forbidden = True
    ai.dm = {"text": "Samokat narxi 1 250 000 so'm.", "answered": True, "topic": "narx"}
    fake.comments["m1"].append({"id": "c22", "text": "Narxi?", "username": "u22", "timestamp": ts(1)})
    events = r.run_once()
    err = next(e for e in events if e["cid"] == "c22" and e["type"].startswith("dm_"))
    check(err["type"] == "dm_error" and "instagram_business_manage_messages" in err["error"],
          "Ruxsat yo'q bo'lsa tushunarli xato qaytdi")
    fake.dm_forbidden = False
    fake.comments["m1"].append({"id": "c23", "text": "Zo'r ekan", "username": "u23", "timestamp": ts(1)})
    events = r.run_once()
    check(not any(e["type"].startswith("dm") for e in events), "Maqtov komentiga direkt yozilmadi")
    r.set_dm_mode("off")
    fake.comments["m1"].append({"id": "c24", "text": "Narxi?", "username": "u24", "timestamp": ts(1)})
    events = r.run_once()
    check(not any(e["type"].startswith("dm") for e in events), "O'chiq rejimda direkt yozilmadi")

    print("\n5) Vazifalar va tezlik")
    r.set_dm_mode("off")
    catalog.set_task("m1", "Layk bosishni eslat")
    catalog.set_task(None, "Obuna bo'lishni eslat")
    fake.comments["m1"].append({"id": "c30", "text": "Juda zo'r narsa ekan bolalarga", "username": "u30", "timestamp": ts(1)})
    r.run_once()
    check("EGASINING VAZIFASI" in ai.last_reply_msg and "Shu post uchun: Layk bosishni eslat" in ai.last_reply_msg
          and "Hamma postlar uchun: Obuna" in ai.last_reply_msg, "Post va umumiy vazifa AI'ga uzatildi")
    check("OXIRGI JAVOBLARIMIZ" in ai.last_reply_msg, "Takrorlamaslik uchun oxirgi javoblar uzatildi")
    catalog.set_post_info("m1", {"title": "Samokat", "price": "1 250 000 so'm"})
    check(catalog.get_post_info("m1")["task"] == "Layk bosishni eslat", "Narx tahrirlanganda vazifa saqlanib qoldi")
    r.run_once()
    before = len(fake.calls)
    r.run_once()
    extra = [c[1] for c in fake.calls[before:]]
    check(all(p == "me/media" for p in extra), f"Komentlar o'zgarmasa faqat postlar ro'yxati so'raladi ({len(extra)} so'rov)")
    r._recent.clear()
    fake.comments["m1"].append({"id": "c31", "text": "Rahmat sizlarga katta, bolam xursand", "username": "u31", "timestamp": ts(1)})
    check(any(e["cid"] == "c31" for e in r.run_once()), "Yangi komment darhol ilg'andi")
    catalog.set_task("m1", "")
    catalog.set_task(None, "")

    print("\n6) HUD API")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import webapp
    from core.jarvis import Jarvis
    from tests.test_webapp import BOT_TOKEN, OWNER, sign
    webapp.init(Jarvis(ai_provider=DmAI(), memory_dir=os.environ["DATA_DIR"] + "/m"), r, lambda: OWNER, BOT_TOKEN)
    app = FastAPI()
    app.include_router(webapp.router)
    c = TestClient(app)
    H = {"X-Init-Data": sign(OWNER)}
    d = c.get("/api/posts", headers=H).json()
    check(len(d["posts"]) == 3 and d["posts"][0]["time"] and d["posts"][0]["info"]["price"] == "1 250 000 so'm",
          "Postlar sana-vaqt va kiritilgan ma'lumot bilan ko'rinadi")
    r2 = c.post("/api/posts/m2/info", json={"title": "Kolleksiya", "price": "99 000 so'm", "info": "",
                                            "use_caption": True}, headers=H).json()
    check(r2["info"]["price"] == "99 000 so'm", "Postga narx HUD orqali kiritildi")
    check(c.post("/api/shop", json={"address": "Yangiyer"}, headers=H).json()["shop"] == {"address": "Yangiyer"},
          "Do'kon ma'lumotlari HUD orqali saqlandi")
    check(c.post("/api/dm/mode", json={"mode": "approve"}, headers=H).json()["status"]["dm_mode"] == "approve",
          "Direkt rejimi HUD orqali o'zgardi")
    c.post("/api/tasks", json={"media_id": "m2", "task": "Do'stini belgilashga unda"}, headers=H)
    c.post("/api/tasks", json={"media_id": None, "task": "Layk"}, headers=H)
    t = c.get("/api/tasks", headers=H).json()
    check(t["global"] == "Layk" and next(p for p in t["posts"] if p["id"] == "m2")["task"].startswith("Do'stini"),
          "Vazifalar HUD orqali saqlandi")
    check(c.get("/api/posts").status_code == 401, "Postlar API imzosiz yopiq")

    print("\n✅ Direkt testlari o'tdi")


if __name__ == "__main__":
    run()
