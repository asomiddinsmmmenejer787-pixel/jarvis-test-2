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

    print("\n✅ O'rganish va belgilash testlari o'tdi")


if __name__ == "__main__":
    run()
