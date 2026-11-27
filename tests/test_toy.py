"""
O'yinchoq profili (ikkinchi Instagram akkaunt) testlari:
- har akkaunt fayllari alohida (token, komment holati, do'kon, xotira)
- o'yinchoq katalogi asosiy sahifadan faqat manzil/telefon va ko'rsatmalarni oladi (narx olinmaydi)
- keshlar (ig user id va h.k.) akkauntlar orasida aralashmaydi
- HUD: X-Account sarlavhasi bilan so'rov o'sha akkauntga tegishli bo'ladi, /api/accounts ikkala profilni beradi
- collab: boshqa ulangan akkauntimiz joylagan postga ikki marta javob yozilmaydi

Ishga tushirish: python -m tests.test_toy
"""

import json
import os
import sys
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="jarvis-toy-")
os.environ.pop("INSTAGRAM_ACCESS_TOKEN", None)
os.environ.pop("INSTAGRAM_TOY_TOKEN", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import accounts  # noqa: E402
import catalog  # noqa: E402
import instagram_api as ig  # noqa: E402

DATA = os.environ["DATA_DIR"]


def test_paths():
    assert accounts.current() == accounts.MAIN
    assert accounts.path("catalog.json") == os.path.join(DATA, "catalog.json")
    with accounts.use(accounts.TOY):
        assert accounts.path("catalog.json") == os.path.join(DATA, "toy", "catalog.json")
        assert accounts.is_toy()
    assert accounts.current() == accounts.MAIN
    assert accounts.normalize("xyz") == accounts.MAIN
    print("  ✅ fayl yo'llari alohida")


def test_tokens():
    os.environ["INSTAGRAM_ACCESS_TOKEN"] = "IGmain" + "a" * 40
    os.environ["INSTAGRAM_TOY_TOKEN"] = "IGtoy" + "b" * 40
    try:
        assert ig.get_token().startswith("IGmain")
        with accounts.use(accounts.TOY):
            assert ig.get_token().startswith("IGtoy")
            assert ig._token_file().endswith(os.path.join("toy", "instagram_token.json"))
        # o'yinchoq ulangan -> asosiy akkaunt collab'da uning postini o'tkazib yuboradi
        assert accounts.other_connected_usernames() == {"bolalar_dunyosi_oyinchoq"}
        with accounts.use(accounts.TOY):
            assert accounts.other_connected_usernames() == {"bolalar_dunyosi.uzb"}
    finally:
        os.environ.pop("INSTAGRAM_ACCESS_TOKEN", None)
        os.environ.pop("INSTAGRAM_TOY_TOKEN", None)
    assert accounts.other_connected_usernames() == set()
    print("  ✅ tokenlar alohida")


def test_catalog_seed():
    catalog.set_shop({"address": "Toshkent, Chilonzor 9", "phone": "+998 77 499 47 44"})
    catalog.add_memory("Rolikli krossovka — 389 000 so'm")
    catalog.save_stock_item({"name": "Yadea G100", "type": "Samokat", "price": "7 200 000", "in_stock": True})
    with accounts.use(accounts.TOY):
        d = catalog.load()
        assert d["shop"]["address"] == "Toshkent, Chilonzor 9", d["shop"]
        assert d["memory"] == [] and d["stock"] == [], "narx/mahsulot asosiy sahifadan olinmasligi kerak"
        catalog.add_memory("Gapiradigan qo'g'irchoq — 189 000 so'm")
        assert catalog.get_memory() == ["Gapiradigan qo'g'irchoq — 189 000 so'm"]
    assert catalog.get_memory() == ["Rolikli krossovka — 389 000 so'm"]
    assert os.path.exists(os.path.join(DATA, "toy", "catalog.json"))
    print("  ✅ do'kon ma'lumoti olinadi, narx va xotira alohida")


def test_scoped_cache():
    ig._ig_user_id["value"] = "111"
    with accounts.use(accounts.TOY):
        assert ig._ig_user_id["value"] is None
        ig._ig_user_id["value"] = "222"
    assert ig._ig_user_id["value"] == "111"
    with accounts.use(accounts.TOY):
        assert ig._ig_user_id["value"] == "222"
    ig._ig_user_id["value"] = None
    with accounts.use(accounts.TOY):
        ig._ig_user_id["value"] = None
    print("  ✅ keshlar aralashmaydi")


def test_responders_separate():
    from auto_reply import CommentResponder
    from dm_chat import DirectChat

    class AI:
        def generate_json(self, *a, **k):
            return {}

    main = CommentResponder(AI())
    with accounts.use(accounts.TOY):
        toy = CommentResponder(AI(), default_mode="on")
        toy_chat = DirectChat(AI())
    assert toy.state.path.endswith(os.path.join("toy", "auto_reply_state.json"))
    assert toy_chat.path.endswith(os.path.join("toy", "dm_chat_state.json"))
    assert toy.mode == "on"
    main.set_mode("off")
    with accounts.use(accounts.TOY):
        assert CommentResponder(AI()).mode == "on"
    print("  ✅ komment/direkt holati alohida")


def test_collab_skip():
    from auto_reply import CommentResponder

    class AI:
        def generate_json(self, *a, **k):
            return {}

    orig = ig.get_collab_posts
    ig.get_collab_posts = lambda n=5: [
        {"id": "c1", "username": "bolalar_dunyosi.uzb", "permalink": "p1"},
        {"id": "c2", "username": "some_blogger", "permalink": "p2"},
    ]
    os.environ["INSTAGRAM_ACCESS_TOKEN"] = "IGmain" + "a" * 40
    try:
        with accounts.use(accounts.TOY):
            r = CommentResponder(AI())
            ids = [p["id"] for p in r._collab_posts()]
        assert ids == ["c2"], ids  # asosiy sahifa postiga asosiy sahifa o'zi javob beradi
    finally:
        ig.get_collab_posts = orig
        os.environ.pop("INSTAGRAM_ACCESS_TOKEN", None)
    print("  ✅ collab: ikki marta javob yozilmaydi")


def test_webapp_accounts():
    import hashlib
    import hmac
    import time
    from urllib.parse import urlencode

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import webapp
    from auto_reply import CommentResponder
    from core.jarvis import Jarvis
    from dm_chat import DirectChat

    class AI:
        def generate_json(self, *a, **k):
            return {}

        def generate(self, *a, **k):
            return "ok"

    bot_token = "123:abc"
    owner = 42
    main_r = CommentResponder(AI())
    webapp.init(Jarvis(ai_provider=AI(), memory_dir=os.path.join(DATA, "m")), main_r, lambda: owner, bot_token,
                DirectChat(AI()))
    with accounts.use(accounts.TOY):
        toy_r = CommentResponder(AI(), default_mode="on")
        webapp.set_account(accounts.TOY, toy_r, DirectChat(AI()))
    main_r.set_mode("test")

    pairs = {"auth_date": str(int(time.time())), "user": json.dumps({"id": owner})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    init = urlencode(pairs)

    app = FastAPI()
    app.include_router(webapp.router)
    c = TestClient(app)
    r = c.get("/api/accounts", headers={"X-Init-Data": init}).json()
    accs = {a["acc"]: a for a in r["accounts"]}
    assert set(accs) == {"main", "toy"}
    assert accs["main"]["mode"] == "test" and accs["toy"]["mode"] == "on"
    assert accs["toy"]["username"] == "bolalar_dunyosi_oyinchoq" and accs["toy"]["connected"] is False

    a = c.get("/api/comments", headers={"X-Init-Data": init}).json()
    b = c.get("/api/comments", headers={"X-Init-Data": init, "X-Account": "toy"}).json()
    assert a["status"]["mode"] == "test" and b["status"]["mode"] == "on"

    c.post("/api/memory", json={"text": "Lego 6+ — 250 000 so'm"}, headers={"X-Init-Data": init, "X-Account": "toy"})
    with accounts.use(accounts.TOY):
        assert "Lego 6+ — 250 000 so'm" in catalog.get_memory()
    assert "Lego 6+ — 250 000 so'm" not in catalog.get_memory()

    c.post("/api/comments/mode", json={"mode": "off"}, headers={"X-Init-Data": init, "X-Account": "toy"})
    assert toy_r.mode == "off" and main_r.mode == "test"
    print("  ✅ HUD: X-Account bo'yicha alohida, /api/accounts ikkala profilni beradi")


def test_collab_one_reply():
    """Ikkala akkaunt hammuallif bo'lgan collab videoda bitta komentga bitta javob."""
    from datetime import datetime, timedelta, timezone

    from auto_reply import CommentResponder

    def ts(h):
        return (datetime.now(timezone.utc) - timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%S+0000")

    class AI:
        n = 0

        def generate_json(self, system, message, temperature=0.9):
            AI.n += 1
            return {"category": "positive", "confidence": 0.95,
                    "reply": ["Rahmat, xursand bo'ldik", "Yoqqanidan xursandmiz", "Sizga ham rahmat"][AI.n % 3]}

    collab = {"id": "blog1", "username": "blogger_ona", "comments_count": 3, "permalink": "p",
              "timestamp": ts(3)}
    comments = [
        {"id": "k1", "text": "Qanday chiroyli 😍", "username": "mijoz1", "timestamp": ts(1)},
        {"id": "k2", "text": "Zo'r ekan", "username": "bolalar_dunyosi_oyinchoq", "timestamp": ts(1)},
        {"id": "k3", "text": "Menga ham yoqdi", "username": "mijoz2", "timestamp": ts(1),
         "replies": {"data": [{"id": "x", "username": "bolalar_dunyosi.uzb", "text": "Rahmat"}]}},
    ]
    sent = []
    saved = (ig.get_profile, ig.get_recent_posts, ig.get_collab_posts, ig.get_all_comments,
             ig.reply_to_comment, ig.get_all_posts)
    ig.get_recent_posts = lambda n=10: []
    ig.get_all_posts = lambda limit=3000: []
    ig.get_collab_posts = lambda n=5: [dict(collab)]
    ig.get_all_comments = lambda mid, fields=None, limit=500: comments if mid == "blog1" else []
    os.environ["REPLY_DELAY_MIN"] = os.environ["REPLY_DELAY_MAX"] = "0"
    try:
        ig.get_profile = lambda: {"username": "bolalar_dunyosi.uzb"}
        main = CommentResponder(AI(), state_path=os.path.join(DATA, "cm_main.json"), default_mode="on")
        main.set_dm_mode("off")
        ig.reply_to_comment = lambda cid, text: sent.append(("main", cid))
        main.run_once()
        with accounts.use(accounts.TOY):
            ig.get_profile = lambda: {"username": "bolalar_dunyosi_oyinchoq"}
            toy = CommentResponder(AI(), state_path=os.path.join(DATA, "toy", "cm_toy.json"), default_mode="on")
            toy.set_dm_mode("off")
            ig.reply_to_comment = lambda cid, text: sent.append(("toy", cid))
            toy.run_once()
    finally:
        (ig.get_profile, ig.get_recent_posts, ig.get_collab_posts, ig.get_all_comments,
         ig.reply_to_comment, ig.get_all_posts) = saved
    assert sent == [("main", "k1")], sent
    print("  ✅ collab: bitta komentga faqat bitta akkaunt javob yozdi, o'z akkauntimiz komentiga javob yo'q")


if __name__ == "__main__":
    test_paths()
    test_tokens()
    test_catalog_seed()
    test_scoped_cache()
    test_responders_separate()
    test_collab_skip()
    test_webapp_accounts()
    test_collab_one_reply()
    print("✅ O'yinchoq profili testlari o'tdi")
