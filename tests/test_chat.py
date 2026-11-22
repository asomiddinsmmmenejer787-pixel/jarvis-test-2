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
    check(chat.status()["price_only"], "Standart: direktda faqat narx so'rovlariga javob (egasi 21.11)")
    chat.set_price_only(False)  # quyidagi bo'limlar umumiy himoyalarni sinaydi
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

    print("\n3c2) Kuzatuv 20.11: 'samakat yo'q' takrori va haqorat")
    chat.set_mode("auto")
    chat.s["counts"] = {}
    u12 = {"id": "U12", "username": "baba"}
    t0 = time.time()
    q1 = {"id": "zk1", "from": u12, "message": "Elektron samaqat", "created_time": t0 - 60}
    a1 = {"id": "zk2", "from": ME, "message": "Samakat hozirda omborda yo'q, sotuvda mavjud emas.", "created_time": t0 - 50}
    q2 = {"id": "zk3", "from": u12, "message": "Keremidiiii", "created_time": t0 - 2}
    ig.record_sent({"message_id": "zk2"}, a1["message"])
    fake.conversations = [{"id": "t12", "messages": {"data": [q2, a1, q1]}}]
    ai.res = {"text": "Elektron samakatlarimiz hozirda omborda yo'q, shuning uchun sizga yordam bera olmaymiz.",
              "kind": "answer", "topic": "samakat"}
    n = len(fake.chat_sent)
    events = chat.run_once()
    check(events and events[0]["type"] == "chat_approval" and "takrorla" in events[0]["reason"]
          and len(fake.chat_sent) == n, "Boshqa so'z bilan 'yana yo'q' deyish takror deb egasiga topshirildi")
    check(not dm_chat.repeats_stock_out("33 razmer ham yo'q, 35 bor.", ["32 razmer yo'q, 34 bor."])
          and not dm_chat.repeats_stock_out("Hech qisi yo'q, buyurtma bering!", ["Samokat hozir yo'q."])
          and not dm_chat.repeats_stock_out("Krasofka narxi 300 ming so'm.", ["Samokat yo'q."]),
          "Boshqa o'lcham yoki boshqa gap takror hisoblanmadi")
    q3 = {"id": "zk4", "from": u12, "message": "Dolbayla", "created_time": t0}
    fake.conversations = [{"id": "t12", "messages": {"data": [q3, a1, q1]}}]
    ai.res = {"text": "Tushunishingizni so'raymiz.", "kind": "answer", "topic": "x"}
    events = chat.run_once()
    check(events and events[0]["type"] == "chat_approval" and "qo'pol" in events[0]["reason"]
          and len(fake.chat_sent) == n, "Haqorat yozgan mijozga Jarvis o'zi javob bermadi, egasiga topshirildi")

    print("\n3d) Kuzatuvdagi xatolar: begona reel va 'Zo'rku'")
    chat.set_mode("auto")
    chat.s["counts"] = {}
    u11 = {"id": "U11", "username": "gul"}
    foreign = {"id": "h1", "from": u11, "created_time": time.time() - 1,
               "shares": {"data": [{"link": "https://www.instagram.com/reel/ZZZ9/?igsh=y"}]}}
    fake.conversations = [{"id": "t11", "messages": {"data": [foreign]}}]
    ai.res = {"text": "Buning narxini bilmoqchimisiz yoki boshqa ma'lumot kerakmi?", "kind": "clarify", "topic": "x"}
    n = len(fake.chat_sent)
    events = chat.run_once()
    check(events == [] and len(fake.chat_sent) == n,
          "Boshqa sahifaning reelini matnsiz ulashgan mijozga 'narxini bilmoqchimisiz' yozilmadi")
    att = {"id": "h2", "from": u11, "created_time": time.time(),
           "attachments": {"data": [{"type": "ig_reel", "payload": {"reel_video_id": "77", "title": "ONAM"}}]}}
    check(dm_chat.is_foreign_share(att) and "boshqa post ulashdi" in dm_chat.describe(att)[0],
          "Ilova ko'rinishidagi begona reel ham aniqlandi")
    check(not dm_chat.is_foreign_share(only_share), "Bizning reelimiz begona deb hisoblanmadi")
    check(not dm_chat.is_foreign_share({"id": "h3", "message": "Bu bormi?", "shares": foreign["shares"]}),
          "Matn bilan ulashilgan postga javob berish to'xtatilmadi")

    u12 = {"id": "U12", "username": "b"}
    gw = {"id": "k1", "from": ME, "created_time": time.time() - 30,
          "message": "Rolikli krasofkalarimizni yutib olish uchun obuna bo'lib, shartlarni bajarishingiz kerak. "
                     "Tabriklaymiz, siz bizning o'yinimizda ishtirokchi bo'ldingiz!"}
    ig.record_sent({"message_id": "k1"}, gw["message"])
    zor = {"id": "k2", "from": u12, "message": "Zorku", "created_time": time.time() - 1}
    fake.conversations = [{"id": "t12", "messages": {"data": [zor, gw]}}]
    ai.res = {"text": "Rahmat, sizga qaysi biri kerak? Telegram guruhimizda barcha modellar: "
                      "https://t.me/bolalar_dunyosiuzb", "kind": "answer", "topic": "x"}
    events = chat.run_once()
    check(events and events[0]["type"] == "chat_sent" and "?" not in fake.chat_sent[-1][1]
          and "t.me" not in fake.chat_sent[-1][1] and "Omad" in fake.chat_sent[-1][1],
          "'Zo'rku' ga sotuv savoli/havola emas, tanlov uchun omad tilandi")
    check(dm_chat.is_reaction("Zo'rku") and dm_chat.is_reaction("Katta rahmat 👍") and dm_chat.is_reaction("Спасибо")
          and not dm_chat.is_reaction("Narxi qancha") and not dm_chat.is_reaction("zor?")
          and not dm_chat.is_reaction("Ha"), "Munosabat va savol farqlandi")
    ask_prev = {"id": "k3", "from": ME, "message": "Katalog havolasini yuboraymi?", "created_time": time.time() - 5}
    ig.record_sent({"message_id": "k3"}, ask_prev["message"])
    okm = {"id": "k4", "from": {"id": "U13", "username": "d"}, "message": "ok", "created_time": time.time()}
    fake.conversations = [{"id": "t13", "messages": {"data": [okm, ask_prev]}}]
    ai.res = {"text": "Mana: https://t.me/bolalar_dunyosiuzb", "kind": "answer", "topic": "x"}
    chat.run_once()
    check("t.me" in fake.chat_sent[-1][1], "Savolimizga 'ok' deb javob bersa, so'ralgan havola yuborildi")

    # Kuzatuv 20.11: "kelishtirishni iloji yo'qmi" ga "Bor, buyurtma berish uchun..." — chegirma va'dasiday
    bg = {"id": "k5", "from": {"id": "U14", "username": "kadirov"}, "created_time": time.time(),
          "message": "Qimatli qildi qizim rosa hohlavitudi kelishtirishni iloji yo'qmi"}
    fake.conversations = [{"id": "t14", "messages": {"data": [bg]}}]
    ai.res = {"text": "Bor, buyurtma berish uchun +998 77 499 47 44 raqamga murojaat qiling.", "kind": "order", "topic": "x"}
    n = len(fake.chat_sent)
    events = chat.run_once()
    check(events and events[0]["type"] == "chat_approval" and len(fake.chat_sent) == n
          and "kelishmoqchi" in events[0]["reason"],
          "Savdolashishga 'Bor' deb yozilmadi — chegirmani egasi hal qiladi")
    bg2 = {"id": "k6", "from": {"id": "U15", "username": "dilnoza2"}, "message": "Kelishtirazmi", "created_time": time.time()}
    fake.conversations = [{"id": "t15", "messages": {"data": [bg2]}}]
    ai.res = {"text": "Narxlarimiz qat'iy, lekin sifatiga ishonchimiz komil.", "kind": "answer", "topic": "x"}
    events = chat.run_once()
    check(events and events[0]["type"] == "chat_sent", "Kelishish so'roviga 'narx qat'iy' javobi o'zi yuborildi")
    check(dm_chat.is_bargain("skidka bormi") and not dm_chat.is_bargain("qachon yetib kelishi")
          and not dm_chat.is_bargain("33 razmer bormi") and not dm_chat.bargain_agreed("Kechirasiz, chegirma yo'q"),
          "Savdolashish va rozilik to'g'ri aniqlandi")

    print("\n3e) Egasi 21.11: direktda faqat narx so'raganlarga javob, keyin suhbat to'xtaydi, hammasi xotirada")
    chat.set_price_only(True)
    chat.set_mode("auto")
    chat.s["counts"] = {}
    ai.last = None
    u20 = {"id": "U20", "username": "malika"}
    t0 = time.time()
    fake.conversations = [conv("t20", ("pp1", u20, "Assalomu alaykum, yetkazib berish bormi?", 2))]
    n = len(fake.chat_sent)
    events = chat.run_once()
    check(events == [] and len(fake.chat_sent) == n and ai.last is None,
          "Narx so'ramagan xabarga Jarvis yozmadi (AI ham chaqirilmadi)")
    fake.conversations = [conv("t20", ("pp1", u20, "Assalomu alaykum, yetkazib berish bormi?", 3),
                               ("pp2", u20, "Samokat S10 narxi qancha?", 1))]
    ai.res = {"text": "Samokat S10 narxi 1 250 000 so'm. Qaysi rangini olasiz?", "kind": "answer", "topic": "narx"}
    events = chat.run_once()
    check(events and events[0]["type"] == "chat_sent" and fake.chat_sent[-1] == ("U20", "Samokat S10 narxi 1 250 000 so'm."),
          "Narx so'roviga faktlardan javob berildi, oxiridagi savol olib tashlandi")
    ig.record_sent({"message_id": "pp3"}, fake.chat_sent[-1][1])
    fake.conversations = [conv("t20", ("pp2", u20, "Samokat S10 narxi qancha?", 5),
                               ("pp3", ME, "Samokat S10 narxi 1 250 000 so'm.", 4), ("pp4", u20, "Rahmat, o'ylab ko'raman", 1))]
    n = len(fake.chat_sent)
    check(chat.run_once() == [] and len(fake.chat_sent) == n, "Narx aytilgach suhbat to'xtadi: keyingi xabarga yozilmadi")
    # Mahsulot noaniq: Jarvis qaysi mahsulot deb so'radi — mijoz javobi narx suhbatining davomi
    u21 = {"id": "U21", "username": "sardor"}
    ai.res = {"text": "Qaysi mahsulot qiziqtirdi?", "kind": "clarify", "topic": "narx"}
    fake.conversations = [conv("t21", ("pq1", u21, "Narxi necha pul", 2))]
    events = chat.run_once()
    check(events and fake.chat_sent[-1] == ("U21", "Qaysi mahsulot qiziqtirdi?"), "Noaniq narx so'roviga mahsulot so'raldi")
    ig.record_sent({"message_id": "pq2"}, "Qaysi mahsulot qiziqtirdi?")
    ai.res = {"text": "Samokat S10 narxi 1 250 000 so'm.", "kind": "answer", "topic": "narx"}
    fake.conversations = [conv("t21", ("pq1", u21, "Narxi necha pul", 5), ("pq2", ME, "Qaysi mahsulot qiziqtirdi?", 4),
                               ("pq3", u21, "Samokat S10", 1))]
    events = chat.run_once()
    check(events and fake.chat_sent[-1] == ("U21", "Samokat S10 narxi 1 250 000 so'm."),
          "Mijoz mahsulot nomini yozgach narx aytildi")
    # 22.11: bitta mijoz bizning postni ulashdi, Instagram uni ikki yozishma qilib qaytardi -> ikki xil javob ketgan edi
    u30 = {"id": "U30", "username": "solish"}
    ai.res = {"text": "Qaysi mahsulot qiziqtirdi?", "kind": "clarify", "topic": "aniqlashtirish"}
    before = len(fake.chat_sent)
    fake.conversations = [conv("t30", ("dd1", u30, "Narxi qancha?", 2)),
                          conv("t31", ("dd2", u30, "Narxi qancha?", 1))]
    events = chat.run_once()
    check(len([e for e in events if e["type"] == "chat_sent"]) == 1
          and len([x for x in fake.chat_sent[before:] if x[0] == "U30"]) == 1,
          "Bir mijozga bir tekshiruvda faqat bitta javob ketdi (ikki yozishma bo'lsa ham)")
    st = chat.status()["memory"]
    mem = dm_chat.DialogMemory(chat.memory.path)  # fayldan qayta o'qiladi
    d20 = mem.d.get("U20", {})
    ids = [x["id"] for x in d20.get("msgs", [])]
    check(st["users"] >= 2 and sorted(ids) == ["pp1", "pp2", "pp3", "pp4"]
          and {x["id"]: x["who"] for x in d20["msgs"]}["pp3"] == "jarvis" and d20["username"] == "malika",
          "Javob berilmagan xabarlar ham, Jarvis javobi ham xotirada saqlandi (takrorsiz)")
    check(any("Mijoz: Rahmat, o'ylab ko'raman" in x for x in mem.dialogs()), "Yozishmalar o'rganish uchun matn ko'rinishida olinadi")
    for t, want in [("Narxi qancha?", True), ("nechpul", True), ("Цена?", True), ("сколько стоит", True),
                    ("Puli qancha", True), ("qanchadan", True), ("Pulimi", False), ("Pulimni qaytaring", False),
                    ("Qancha kunda keladi", False), ("Bormi?", False), ("Rahmat", False),
                    ("Oyiga", True), ("Rassrochka bormi", True), ("Nasiyaga bersa bo'ladimi", True),
                    ("Bo'lib to'lasa bo'ladimi", True), ("рассрочка есть?", True), ("Oyim uchun", False),
                    ("Rostan berasizmi", False)]:
        if dm_chat.is_price_question(t) != want:
            check(False, f"Narx so'rovi aniqlash: {t!r}")
    check(True, "Narx so'rovi to'g'ri aniqlandi (bo'lib to'lash ham; pullik tanlov, pul qaytarish, muddat narx emas)")
    chat.set_price_only(False)

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
