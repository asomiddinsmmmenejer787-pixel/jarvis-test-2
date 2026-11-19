"""
tests/test_brain.py
-------------------
Belgilangan (@) komentlar, egasining ko'rsatmalari va qayta tekshiruv testlari.

Ishga tushirish:
    python -m tests.test_brain
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="jarvis-brain-")
os.environ["INSTAGRAM_ACCESS_TOKEN"] = "ENV_TOKEN_1"
os.environ["REPLY_DELAY_MIN"] = "0"
os.environ["REPLY_DELAY_MAX"] = "0"

import auto_reply  # noqa: E402
import brain  # noqa: E402
import instagram_api as ig  # noqa: E402
from tests.test_instagram import FakeInstagram, check, ts  # noqa: E402


class TagAI:
    def __init__(self):
        self.messages = []
        self.spam = True

    def generate_json(self, system, message, temperature=0.9):
        self.messages.append(message)
        if "EGASINING XABARI" in message:
            return {"kind": "work", "reply": "Tushundim, tuzataman.",
                    "lesson": "Belgilangan komentga ikkalasiga qisqa javob yoz.",
                    "fact": "", "actions": [{"type": "recheck_comments", "posts": 9}]}
        text = message.split("<komment>")[1].split("</komment>")[0].lower()
        if "@" in text and self.spam:
            return {"category": "spam", "confidence": 0.9, "reply": ""}
        if "@" in text:
            return {"category": "tag", "confidence": 0.9, "reply": "@lola Ikkalangizga ham yoqadi!"}
        if "?" in text:
            return {"category": "price", "confidence": 0.9, "reply": "Narxini direktga yozdik."}
        return {"category": "positive", "confidence": 0.9, "reply": "Rahmat, qaysi biri yoqdi?"}


def run():
    fake = FakeInstagram()
    ig.requests.request = fake.request
    fake.comments = {"m1": [
        {"id": "t1", "text": "@lola_88 qara", "username": "ona_1", "timestamp": ts(1)},
        {"id": "t2", "text": "Zo'r @bolalar_dunyosi.uzb", "username": "dada", "timestamp": ts(1)},
        {"id": "t3", "text": "@a @b @c http://spam.uz", "username": "bot", "timestamp": ts(1)},
    ], "m2": []}
    ai = TagAI()
    r = auto_reply.CommentResponder(ai)
    r.set_mode("on")
    r.set_dm_mode("off")

    print("\n1) Belgilangan komentlar")
    check(auto_reply.mentions_in("@lola_88 qara @x. @me", "me", "ona") == ["lola_88", "x"], "Belgilanganlar ajratildi")
    r.run_once()
    sent = dict(fake.replies)
    check(sent.get("t1", "").startswith("@ona_1 @lola_88 ") and len(sent["t1"]) < 140,
          "AI spam desa ham faqat belgilash komentiga ikkalasiga qisqa javob yozildi")
    check("t3" not in sent, "Linkli belgilash baribir spam")
    check("@bolalar_dunyosi.uzb" not in sent.get("t2", ""), "O'z akkauntimiz belgilansa, javobda tilga olinmaydi")
    ai.spam = False
    # t1 ga tasodifiy zaxira javob tanlanadi; u "Ikkalangizga ham yoqadi..." bo'lsa,
    # t4 takror deb o'tkazib yuboriladi. Test barqaror bo'lishi uchun takror tarixini tozalaymiz.
    r._recent.clear()
    fake.comments["m1"].append({"id": "t4", "text": "@lola bu senga", "username": "ona_2", "timestamp": ts(1)})
    r.run_once()
    check(dict(fake.replies)["t4"] == "@ona_2 @lola Ikkalangizga ham yoqadi!",
          "AI yozgan @ olib tashlandi, ism tizim tomonidan to'g'ri qo'shildi")

    print("\n2) Ko'rsatmalar va qayta tekshiruv")
    lessons = brain.add_lesson("Javoblar qisqa bo'lsin.")
    check(lessons == ["Javoblar qisqa bo'lsin."], "Qoida saqlandi")
    res = brain.think(ai, "Belgilanganlarga javob bermayapsan, qayta tekshir")
    check(res["kind"] == "work" and res["lesson"] and res["actions"] == [{"type": "recheck_comments", "posts": 5}],
          "Egasining tanbehi qoida va harakatga aylandi (5 postdan oshmaydi)")
    r.state.done["t3"] = "skipped"
    r.set_policy(short_ratio=100, low_limit=1000, per_user_day=10)
    ai.spam = True
    fake.comments["m1"].append({"id": "t5", "text": "Juda chiroyli", "username": "x5", "timestamp": ts(60)})
    n = len(fake.replies)
    events, stats = r.recheck(1)
    check(any("EGASINING KO'RSATMALARI" in m and "Javoblar qisqa" in m for m in ai.messages[-3:]),
          "Ko'rsatma komment javobiga qo'shildi")
    check(any(e.get("cid") == "t5" for e in events) and len(fake.replies) > n,
          "Qayta tekshiruv 24 soatdan eski javobsiz komentga ham javob yozdi")
    check(r.state.done.get("t3") == "skipped", "Spam qayta ko'rildi va yana spam deb belgilandi")
    brain.log_event({"type": "sent", "username": "ona_1", "text": "@lola qara", "reply": "Salom"})
    check("Komentga javob: @ona_1" in brain.journal_text(), "Jurnalga yozildi")
    check(brain.delete_lesson(0) == "Javoblar qisqa bo'lsin." and not brain.get_lessons(), "Qoida o'chirildi")

    lines, rc = brain.apply({"reply": "Xo'p", "actions": [{"type": "set_limit", "value": 500},
                                                         {"type": "recheck_comments", "posts": 2}]}, r)
    check(r.day_limit() == 500 and any("500" in x for x in lines) and rc == 2,
          "Chatda limit so'ralsa haqiqatan o'zgaradi")

    print("\n3) Limitni tejash")
    r.state.done.clear()
    r.state.daily = {"date": "", "count": 0}
    r._recent.clear()
    r.set_policy(low_limit=1, short_ratio=100)
    fake.comments["m1"] = [
        {"id": "p1", "text": "@dugona1 qara", "username": "aa", "timestamp": ts(1)},
        {"id": "p2", "text": "@dugona2 qara", "username": "bb", "timestamp": ts(1)},
        {"id": "p3", "text": "Narxi qancha bu mashinani?", "username": "bb", "timestamp": ts(1)},
    ]
    fake.comments["m2"] = []
    asked = len(ai.messages)
    n = len(fake.replies)
    r._counts.clear()
    r.run_once()
    sent = [c for c, _ in fake.replies[n:]]
    check("p1" in sent and "p2" not in sent and r.state.done.get("p2") == "budget",
          "Belgilashlarga alohida limit ishladi (1 ta)")
    check("p3" in sent, "Limit tugasa ham savolga javob yozildi")
    check(len(ai.messages) - asked == 2, "Limitdan oshgan belgilash AI'ga yuborilmadi (tejaldi)")
    r.set_policy(skip=["positive", "xato"], per_user_day=99)
    check(r.policy["skip"] == ["positive"] and r.policy["per_user_day"] == 10, "Qoidalar tekshirilib saqlandi")
    lines, _ = brain.apply({"reply": "Xo'p", "actions": [{"type": "set_policy", "changes": {"low_limit": 50}}]}, r)
    check(r.policy["low_limit"] == 50 and "50" in lines[-1], "Chat orqali tejash qoidasi o'zgardi")
    check(auto_reply.is_short("zo'r 🔥") and not auto_reply.is_short("Narxi qancha?")
          and not auto_reply.is_short("Narxi qancha") and not auto_reply.is_short("bormi"), "Qisqa komment aniqlandi, savol emas")

    print("\n4) Kuzatuvda topilgan xatolar (19.11)")
    v = auto_reply.assess({"category": "price", "confidence": 0.9, "reply": "Narxi 300 ming so'm"}, "Krasofka")
    check(v["action"] == "auto" and "300" not in v["reply"] and "direkt" in v["reply"].lower(),
          "Narx yozilgan javob ruxsatga emas, xavfsiz javob bilan avtomatik ketadi")
    rec = ["Yuragingizni quvonchga to'ldirdingiz! Obuna bo'lib, tanishlaringizni belgilang."]
    check(auto_reply.too_similar("Yuragingizni quvonchga to'ldirdingiz! Layk bosing", rec)
          and not auto_reply.too_similar("Rahmat, bizga ham yoqdi 😊", rec), "Takroriy javob aniqlanadi")
    import time as _t
    r.state.flags.pop("fix_v3", None)
    r.state.pending = {
        "x1": {"text": "Krasofka", "category": "price", "reason": "Tayyor javob tekshiruvdan o'tmadi", "created": _t.time()},
        "x2": {"text": "Tekinga berin oka 😂", "category": "negative", "reason": "Salbiy komment", "created": _t.time()},
        "x3": {"text": "Bu bekorchi, ahmoq do'kon", "category": "negative", "reason": "x", "created": _t.time()},
        "x4": {"text": "Bizda arzon", "category": "competitor", "reason": "x", "created": _t.time() - 3 * 86400},
    }
    r.state.dm_pending = {"d1": {"created": _t.time() - 2 * 86400}, "d2": {"created": _t.time()}}
    check(r._housekeeping() is True, "Bir martalik tuzatish ishga tushdi")
    check(set(r.state.pending) == {"x3"} and r.state.done.get("x4") == "expired",
          "Noto'g'ri ruxsatga ketganlar qayta ko'rishga qaytdi, haqorat qoldi, eskisi eskirdi")
    check(set(r.state.dm_pending) == {"d2"}, "Eski direkt so'rovlari tozalandi")
    check(r._housekeeping() is False, "Tuzatish faqat bir marta")

    print("\n5) Postga javobni o'chirish va sovg'a so'rash")
    import catalog
    fake.comments["m1"] = [{"id": "q1", "text": "Juda chiroyli ekan bu narsa", "username": "q1", "timestamp": ts(1)}]
    fake.comments["m2"] = []
    r._counts.clear()
    lines, _ = brain.apply({"reply": "Xo'p", "actions": [{"type": "post_reply", "post": 1, "on": False}]}, r)
    off_id = ig.get_recent_posts(1)[0]["id"]
    check(catalog.is_reply_off(off_id) and "⛔" in lines[-1], "Chat orqali postga javob o'chirildi")
    n = len(fake.replies)
    r.run_once()
    check(all(c != "q1" for c, _ in fake.replies[n:]) or off_id != "m1", "O'chirilgan postga komment yozilmadi")
    catalog.set_reply_off(off_id, False)
    f = catalog.post_facts({"id": "zz", "caption": "Tanlov! Obuna bo'ling va yutib oling"})
    check(any("O'YIN/TANLOV POSTI" in x for x in f["lines"]), "O'yin posti aniqlandi")
    f = catalog.post_facts({"id": "zz2", "caption": "Yangi dron keldi"})
    check(any("O'YIN/TANLOV YO'Q" in x for x in f["lines"]), "O'yinsiz post aniqlandi")
    check(not auto_reply.is_short("Qayirda joylashgan") and not auto_reply.is_short("34 rasmer"),
          "Manzil va o'lcham savollari o'tkazib yuborilmaydi")

    print("\n✅ O'rganish va belgilash testlari o'tdi")


if __name__ == "__main__":
    run()
