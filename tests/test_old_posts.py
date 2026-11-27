"""
27.11 egasi: eski postlarga (bir yil oldingi ham) kelgan yangi komentlarga javob; takroriy komentlarga ham javob.
Ishga tushirish: python -m tests.test_old_posts
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="jarvis-old-")
os.environ["REPLY_DELAY_MIN"] = "0"
os.environ["REPLY_DELAY_MAX"] = "0"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auto_reply  # noqa: E402
import instagram_api as ig  # noqa: E402


def ts(hours_ago):
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%S+0000")


class AI:
    n = 0

    def generate_json(self, system, message, temperature=0.9):
        AI.n += 1
        return {"category": "positive", "confidence": 0.95,
                "reply": ["Rahmat, kayfiyatimizni ko'tardingiz", "Yoqqanidan xursandmiz", "Sizga ham rahmat",
                          "Bizni kuzatib boring", "Iliq so'z uchun rahmat"][AI.n % 5]}


def run():
    recent = [{"id": f"r{i}", "comments_count": 0, "timestamp": ts(i)} for i in range(10)]
    old = {"id": "old1", "comments_count": 2, "timestamp": ts(24 * 365), "caption": "O'tgan yilgi video"}
    all_posts = recent + [old, {"id": "old2", "comments_count": 1, "timestamp": ts(24 * 400)}]
    comments = {
        "old1": [{"id": "c1", "text": "Bu hali bormi?", "username": "ona", "timestamp": ts(1)},
                 {"id": "c0", "text": "Zo'r", "username": "eski", "timestamp": ts(24 * 300)}],
        "old2": [{"id": "c2", "text": "😍", "username": "ota", "timestamp": ts(2)}],
    }
    replies = []
    calls = {"all": 0}
    ig.get_profile = lambda: {"username": "bolalar_dunyosi.uzb", "user_id": "1"}
    ig.get_recent_posts = lambda n=10: recent[:n]
    ig.get_collab_posts = lambda n=5: []

    def get_all(limit=3000):
        calls["all"] += 1
        return all_posts
    ig.get_all_posts = get_all
    ig.get_all_comments = lambda mid, fields=None, limit=500: comments.get(mid, [])
    ig.reply_to_comment = lambda cid, text: replies.append(cid)
    ig.send_private_reply = lambda cid, text: None

    r = auto_reply.CommentResponder(AI(), default_mode="on")
    r.set_dm_mode("off")
    r.run_once()
    assert set(replies) == {"c1", "c2"}, replies   # eski postdagi yangi komentlar; 300 kunlik komment emas
    print("  ✅ bir yillik postlarga kelgan yangi komentlarga javob yozildi")

    # Keyingi tekshiruv: ro'yxat 15 daqiqa kesh, o'zgarmagan post qayta o'qilmaydi
    replies.clear()
    r.run_once()
    assert replies == [] and calls["all"] == 1
    # yangi komment eski postga keldi
    comments["old1"].append({"id": "c3", "text": "😍", "username": "ona", "timestamp": ts(0.1)})
    comments["old1"].append({"id": "c4", "text": "😍😍", "username": "ona", "timestamp": ts(0.05)})
    old["comments_count"] = 4
    r._all_next = 0
    r.run_once()
    assert replies == ["c3", "c4"], replies   # takroriy komentlarga ham javob
    print("  ✅ eski postga keyin kelgan va takroriy komentlarga ham javob")

    # qayta ishga tushganda sonlar saqlangan: eski komentlar qayta ko'rilmaydi
    r2 = auto_reply.CommentResponder(AI())
    assert r2._counts.get("old1") == 4
    print("✅ Eski postlar testlari o'tdi")


if __name__ == "__main__":
    run()
