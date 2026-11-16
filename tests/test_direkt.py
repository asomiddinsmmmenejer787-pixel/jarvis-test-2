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
        return super().generate_json(system, message, temperature)


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
    check(r.dm_mode == "approve", "Standart direkt rejimi: tasdiq")
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
    check(any(e["type"] == "dm_approval" and e["cid"] == "c21" for e in events) and fake.dms[-1][0] != "c21",
          "Shubhali narx bo'lsa avto rejimda ham tasdiqqa tushdi")
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
    fake.comments["m1"].append({"id": "c30", "text": "Zo'r", "username": "u30", "timestamp": ts(1)})
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
    fake.comments["m1"].append({"id": "c31", "text": "Rahmat", "username": "u31", "timestamp": ts(1)})
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
