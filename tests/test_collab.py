"""
tests/test_collab.py
--------------------
23.11: collab (hammualliflik) qilingan video boshqa akkauntda turadi, me/media'da chiqmaydi,
shuning uchun uning komentlariga Jarvis javob yozmas edi.

Ishga tushirish:
    python -m tests.test_collab
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="jarvis-collab-")
os.environ["INSTAGRAM_ACCESS_TOKEN"] = "ENV_TOKEN_1"
os.environ["REPLY_DELAY_MIN"] = os.environ["REPLY_DELAY_MAX"] = "0"

import auto_reply  # noqa: E402
import instagram_api as ig  # noqa: E402
from tests.test_instagram import FakeAI, FakeInstagram, check, ts  # noqa: E402


def run():
    os.environ["AUTO_REPLY_MODE"] = "on"
    fake = FakeInstagram()
    fake.comments = {}
    for p in fake.posts:
        p["comments_count"] = 0
    ig.requests.request = fake.request

    print("\n1) Instagram collab ro'yxatini bermasa asosiy ish to'xtamaydi")
    r = auto_reply.CommentResponder(FakeAI(), state_path=os.path.join(os.environ["DATA_DIR"], "s1.json"))
    r.set_mode("on")
    fake.comments["m1"] = [{"id": "o1", "text": "Zo'r ekan!", "username": "ona_7", "timestamp": ts(1)}]
    r.run_once()
    check(any(c == "o1" for c, _ in fake.replies), "O'z postidagi komentga javob yozildi")
    n_calls = sum(1 for _, path, _ in fake.calls if path == "me/collaborative_media")
    r._counts.clear()
    r.run_once()
    check(sum(1 for _, path, _ in fake.calls if path == "me/collaborative_media") == n_calls == 1,
          "Instagram bermasa collab ro'yxati har daqiqada qayta so'ralmaydi")

    print("\n2) Collab videodagi komentlarga javob yoziladi")
    fake.collab = [{"id": "k1", "caption": "Bloger bilan birga: samokat", "media_type": "VIDEO",
                    "media_product_type": "REELS", "permalink": "https://www.instagram.com/reel/KKK1/",
                    "timestamp": ts(3), "username": "bloger_aka"}]
    fake.comments["k1"] = [
        {"id": "k_c1", "text": "Narxi qancha?", "username": "mijoz_1", "timestamp": ts(1)},
        {"id": "k_c2", "text": "Do'stlar, ko'rib chiqinglar", "username": "bloger_aka", "timestamp": ts(1)},
    ]
    r2 = auto_reply.CommentResponder(FakeAI(), state_path=os.path.join(os.environ["DATA_DIR"], "s2.json"))
    r2.set_mode("on")
    r2.run_once()
    check(r2.last_run.get("collab") == 1, "Collab post tekshiruvga qo'shildi")
    check(any(c == "k_c1" for c, _ in fake.replies), "Collab videodagi savolga javob yozildi")
    check(not any(c == "k_c2" for c, _ in fake.replies), "Videoni joylagan blogerning o'z komentiga javob yozilmadi")

    fake.collab.append(dict(fake.posts[0], username="bolalar_dunyosi.uzb"))
    r3 = auto_reply.CommentResponder(FakeAI(), state_path=os.path.join(os.environ["DATA_DIR"], "s3.json"))
    r3.set_mode("on")
    r3.run_once()
    check(r3.last_run.get("collab") == 1, "O'z postimiz collab ro'yxatida bo'lsa ikki marta tekshirilmaydi")

    print("\ntest_collab: hammasi o'tdi")


if __name__ == "__main__":
    run()
