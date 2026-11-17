"""
tests/test_chat.py
------------------
Direktga o'zi yozgan mijozlarga javob (dm_chat.py) testlari.

Ishga tushirish:
    python -m tests.test_chat
"""

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="jarvis-chat-")
os.environ["INSTAGRAM_ACCESS_TOKEN"] = "ENV_TOKEN_1"

import catalog  # noqa: E402
import dm_chat  # noqa: E402
import instagram_api as ig  # noqa: E402
from tests.test_instagram import FakeInstagram, check  # noqa: E402

ME = {"id": "1", "username": "bolalar_dunyosi.uzb"}


class ChatAI:
    def __init__(self, res):
        self.res = res
        self.last = None

    def generate_json(self, system, message, temperature=0.4):
        self.last = message
        return self.res


def conv(cid, *msgs):
    """msgs: (mid, kimdan, matn, necha soniya oldin) - eskidan yangiga."""
    data = [{"id": mid, "from": frm, "message": text, "created_time": time.time() - ago}
            for mid, frm, text, ago in msgs]
    return {"id": cid, "messages": {"data": list(reversed(data))}}


def run():
    fake = FakeInstagram()
    ig.requests.request = fake.request
    catalog.set_shop({"phone": "+998 77 499 47 44"})
    catalog.set_post_info("m1", {"title": "Samokat S10", "price": "1 250 000 so'm"})
    u1 = {"id": "U1", "username": "dilnoza"}
    u2 = {"id": "U2", "username": "aziz"}

    print("\n1) Bilim bazasi")
    facts, source = dm_chat.knowledge()
    check("Samokat S10" in facts and "1 250 000" in facts and "+998 77 499 47 44" in facts
          and "Yangi kolleksiya" in facts, "Faktlarda do'kon, kiritilgan narx va opisaniyalar bor")

    print("\n2) TASDIQ rejimi")
    ai = ChatAI({"text": "Samokat S10 narxi 1 250 000 so'm.", "kind": "answer", "topic": "samokat narxi"})
    chat = dm_chat.DirectChat(ai)
    check(chat.mode == "auto", "Standart rejim: avto")
    chat.set_mode("approve")
    fake.conversations = [conv("t0", ("old1", u2, "Eski savol", 30))]
    chat.s["since"] = time.time() - 10
    check(chat.run_once() == [], "Tizim yoqilishidan oldingi xabarga javob berilmadi")
    fake.conversations = [
        conv("t1", ("a1", u1, "Assalomu alaykum", 8), ("a2", u1, "Samokat narxi qancha?", 5)),
        conv("t2", ("b1", u2, "Bormi?", 9), ("b2", ME, "Ha, bor", 4)),
        conv("t3", ("c1", {"id": "U3", "username": "x"}, "", 3)),
    ]
    events = chat.run_once()
    check(len(events) == 1 and events[0]["type"] == "chat_approval" and events[0]["username"] == "dilnoza",
          "Faqat javobsiz mijoz xabari olindi (biz javob bergan va matnsiz xabar o'tkazildi)")
    check("Mijoz: Assalomu alaykum\nMijoz: Samokat narxi qancha?" in ai.last, "AI'ga yozishma tarixi uzatildi")
    check(not fake.chat_sent, "Tasdiqsiz hech narsa yuborilmadi")
    check(chat.run_once() == [], "Takror tekshiruvda takrorlanmadi")
    key = chat.get_pending()[0]["key"]
    check(len(f"dc|ok|{key}".encode()) <= 64, "Telegram tugmasi uchun kalit qisqa")
    chat.set_message_id(key, 77)
    check(chat.find_by_message(77) == key, "Telegram xabari yozishmaga bog'landi")
    check(chat.approve(key).startswith("💬") and fake.chat_sent[-1] == ("U1", "Samokat S10 narxi 1 250 000 so'm."),
          "Tasdiqlangach javob mijozga yuborildi")
    check("allaqachon" in chat.approve(key), "Ikki marta yuborilmaydi")

    print("\n3) AVTO rejim va himoya")
    chat.set_mode("auto")
    fake.conversations = [conv("t1", ("a3", u1, "Rahmat, manzil qayer?", 2))]
    ai.res = {"text": "Qaysi mahsulot qiziqtirdi?", "kind": "clarify", "topic": "manzil"}
    events = chat.run_once()
    check(events[0]["type"] == "chat_sent" and fake.chat_sent[-1][1] == "Qaysi mahsulot qiziqtirdi?",
          "Xavfsiz javob darhol yuborildi")
    fake.conversations = [conv("t1", ("a4", u1, "Narxi?", 2))]
    ai.res = {"text": "Narxi 990 000 so'm", "kind": "answer", "topic": "narx"}
    events = chat.run_once()
    check(events[0]["type"] == "chat_sent" and "raqam" in events[0]["reason"]
          and fake.chat_sent[-1] == ("U1", catalog.fallback_dm()),
          "To'qilgan narx yuborilmadi: o'rniga narxsiz xabar ketdi")
    fake.conversations = [conv("t1", ("a5", u1, "Pulimni qaytaring", 1))]
    ai.res = {"text": "Hozir aniqlab, tez orada javob beramiz.", "kind": "human", "topic": "qaytarish"}
    events = chat.run_once()
    check(events[0]["type"] == "chat_sent" and "o'zingiz" in events[0]["reason"] and not chat.get_pending(),
          "Odam kerak bo'lgan holatda mijozga javob ketdi va egasi ogohlantirildi")
    fake.conversations = [conv("t1", ("a6", u1, "Olaman, qanday buyurtma qilaman?", 1))]
    ai.res = {"text": "Ajoyib! Ism va manzilingizni yozing yoki +998 77 499 47 44 ga qo'ng'iroq qiling.",
              "kind": "order", "topic": "buyurtma"}
    events = chat.run_once()
    check(events[0]["type"] == "chat_sent" and events[0]["reason"].startswith("🛒"), "Buyurtma niyati egasiga bildirildi")
    catalog.add_memory("Velosiped — 900 000 so'm")
    check("DO'KON XOTIRASI" in dm_chat.knowledge()[0] and "900 000" in dm_chat.knowledge()[1], "Xotira faktlarga qo'shildi")
    ai.res = {"text": "Ha", "kind": "answer", "topic": "x"}
    for i in range(dm_chat.MAX_AUTO_PER_USER_DAY + 4):
        fake.conversations = [conv("t9", (f"z{i}", u2, f"savol {i}", 1))]
        chat.run_once()
    sent_u2 = [s for s in fake.chat_sent if s[0] == "U2"]
    check(len(sent_u2) == dm_chat.MAX_AUTO_PER_USER_DAY, "Bir mijozga kunlik avtojavob chegarasi ishlaydi")
    fake.dm_forbidden = True
    fake.conversations = [conv("t5", ("e1", {"id": "U5", "username": "n"}, "Salom", 1))]
    events = chat.run_once()
    check(events[0]["type"] == "chat_error" and "ruxsat" in events[0]["error"], "Ruxsat yo'q bo'lsa tushunarli xato")
    fake.dm_forbidden = False
    chat.set_mode("off")
    check(chat.run_once() == [], "O'chiq rejimda ishlamaydi")

    print("\n3b) Ulashilgan post va takrorlanmaslik")
    chat.set_mode("auto")
    chat.s["since"] = time.time() - 60
    chat.s["counts"] = {}
    share = {"id": "s1", "from": {"id": "U7", "username": "ona"}, "created_time": time.time() - 3,
             "shares": {"data": [{"link": "https://www.instagram.com/reel/AAA1/?igsh=x"}]}}
    ask = {"id": "s2", "from": {"id": "U7", "username": "ona"}, "message": "Narxi?", "created_time": time.time() - 1}
    fake.conversations = [{"id": "t7", "messages": {"data": [ask, share]}}]
    ai.res = {"text": "Samokat S10 narxi 1 250 000 so'm.", "kind": "answer", "topic": "narx"}
    chat.run_once()
    check("MIJOZ SO'RAYOTGAN POST" in ai.last and "bizning postimizni ulashdi" in ai.last
          and "Samokat S10" in ai.last.split("MIJOZ SO'RAYOTGAN POST")[1],
          "Mijoz ulashgan reel aniqlandi va o'sha post ma'lumoti AI'ga berildi")
    check(fake.chat_sent[-1] == ("U7", "Samokat S10 narxi 1 250 000 so'm."), "Ulashilgan post narxi javob qilindi")
    only_share = dict(share, id="s3", created_time=time.time())
    fake.conversations = [{"id": "t8", "messages": {"data": [dict(only_share, **{"from": {"id": "U8", "username": "b"}})]}}]
    n = len(fake.chat_sent)
    chat.run_once()
    check(len(fake.chat_sent) == n + 1, "Matnsiz faqat reel ulashgan mijozga ham javob berildi")
    prev = {"id": "r1", "from": ME, "message": "Qaysi mahsulot qiziqtirdi?", "created_time": time.time() - 5}
    again = {"id": "r2", "from": {"id": "U9", "username": "c"}, "message": "Hmm", "created_time": time.time() - 1}
    fake.conversations = [{"id": "t9", "messages": {"data": [again, prev]}}]
    ai.res = {"text": "Qaysi mahsulot qiziqtirdi?", "kind": "clarify", "topic": "x"}
    events = chat.run_once()
    check(events[0]["type"] == "chat_approval" and "takrorla" in events[0]["reason"],
          "Bir xil gapni takrorlash o'rniga egasiga topshirildi")
    ai.res = {"text": "Narxi 777 000 so'm", "kind": "answer", "topic": "x"}
    fake.conversations = [{"id": "t9", "messages": {"data": [dict(again, id="r3", created_time=time.time()), prev]}}]
    events = chat.run_once()
    check(not events[0]["text"].startswith("Assalomu"), "Suhbat o'rtasida zaxira xabar salomlashmaydi")
    check(catalog.numbers_ok("1,25 mln so'm", "Narx: 1 250 000 so'm"), "'1,25 mln' yozuvi to'g'ri narx deb tanildi")

    print("\n3c) Takror va egasi aralashuvi")
    chat.set_mode("auto")
    chat.s["counts"] = {}
    u10 = {"id": "U10", "username": "ona2"}
    t0 = time.time()
    chat.s["human_since"] = t0 - 100
    p1 = {"id": "g1", "from": u10, "message": "Qanaqa shart", "created_time": t0 - 50}
    b1 = {"id": "g2", "from": ME, "message": "Shartlari postda yozilgan. Qaysi o'lcham kerak bo'ladi?",
          "created_time": t0 - 40}
    p2 = {"id": "g3", "from": u10, "message": "Pulimi", "created_time": t0 - 2}
    ig.record_sent({"message_id": "g2"}, b1["message"])
    fake.conversations = [{"id": "t10", "messages": {"data": [p2, b1, p1]}}]
    ai.res = {"text": "Shartlari postda yozilgan, qaysi o'lcham kerak?", "kind": "answer", "topic": "x"}
    events = chat.run_once()
    check(events and events[0]["type"] == "chat_approval" and "takrorla" in events[0]["reason"],
          "Deyarli bir xil javob mijozga ketmadi, egasiga topshirildi")
    owner = {"id": "g4", "from": ME, "attachments": {"data": [{"audio_data": {"url": "x"}}]}, "created_time": t0 - 1}
    p3 = {"id": "g5", "from": u10, "message": "Tushundim, rahmat", "created_time": t0}
    fake.conversations = [{"id": "t10", "messages": {"data": [p3, owner, p2, b1, p1]}}]
    n = len(fake.chat_sent)
    ai.res = {"text": "Arzimaydi!", "kind": "answer", "topic": "x"}
    events = chat.run_once()
    check(events == [] and len(fake.chat_sent) == n, "Egasi o'zi yozgan suhbatga Jarvis aralashmadi")
    check(ig.is_jarvis_message(b1) and not ig.is_jarvis_message(owner), "Jarvis xabari egasinikidan ajratildi")
    check(dm_chat._similar("Narxi 300 ming. Qaysi o'lcham?", "narxi 300 ming, qaysi o'lcham kerak?") > 0.72
          and dm_chat._similar("Ha, ishtirok bepul", "Narxi 300 ming") < 0.5, "O'xshashlik o'lchovi")

    print("\n4) HUD API")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import webapp
    from auto_reply import CommentResponder
    from core.jarvis import Jarvis
    from tests.test_instagram import FakeAI
    from tests.test_webapp import BOT_TOKEN, OWNER, sign
    webapp.init(Jarvis(ai_provider=FakeAI(), memory_dir=os.environ["DATA_DIR"] + "/m"),
                CommentResponder(FakeAI()), lambda: OWNER, BOT_TOKEN, chat)
    app = FastAPI()
    app.include_router(webapp.router)
    c = TestClient(app)
    H = {"X-Init-Data": sign(OWNER)}
    check(c.post("/api/inbox/mode", json={"mode": "approve"}, headers=H).json()["status"]["mode"] == "approve",
          "Rejim HUD orqali o'zgardi")
    fake.conversations = [conv("t6", ("f1", {"id": "U6", "username": "m"}, "Narxi?", 0))]
    ai.res = {"text": "Qaysi mahsulot?", "kind": "clarify", "topic": "narx"}
    chat.run_once()
    d = c.get("/api/comments", headers=H).json()["chat"]
    item = next((p for p in d["pending"] if p["igsid"] == "U6"), None)
    check(item is not None, "Tasdiq kutayotgan yozishma HUD'da ko'rinadi")
    r = c.post(f"/api/inbox/{item['key']}", json={"action": "send", "text": "O'z javobim"}, headers=H).json()
    check(r["message"].startswith("💬") and fake.chat_sent[-1] == ("U6", "O'z javobim"), "HUD'dan javob yuborildi")
    check(c.post("/api/inbox/mode", json={"mode": "auto"}).status_code == 401, "API imzosiz yopiq")

    print("\n✅ Direkt yozishma testlari o'tdi")


if __name__ == "__main__":
    run()
