"""
tests/test_instagram.py
-----------------------
Instagram tahlili va komentlarga javob tizimining testlari.
Haqiqiy Instagram/Gemini kerak emas: soxta (fake) Instagram serveri va soxta AI ishlatiladi.

Ishga tushirish:
    python -m tests.test_instagram
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TMP = tempfile.mkdtemp(prefix="jarvis-test-")
os.environ["DATA_DIR"] = TMP
os.environ["INSTAGRAM_ACCESS_TOKEN"] = "ENV_TOKEN_1"
os.environ["REPLY_DELAY_MIN"] = "0"
os.environ["REPLY_DELAY_MAX"] = "0"
os.environ["AUTO_REPLY_MODE"] = "test"
os.environ.pop("OWNER_CHAT_ID", None)

import instagram_api as ig  # noqa: E402
import analytics  # noqa: E402
import auto_reply  # noqa: E402


def ts(hours_ago):
    t = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return t.strftime("%Y-%m-%dT%H:%M:%S+0000")


class FakeResponse:
    def __init__(self, body, status=200):
        self._body = body
        self.status_code = status

    def json(self):
        return self._body


class FakeInstagram:
    """Graph API'ning soddalashtirilgan nusxasi."""

    INVALID = {"ig_reels_video_view_total_time"}

    def __init__(self):
        self.calls = []
        self.replies = []
        self.hidden = []
        self.token_valid = True
        self.posts = [
            {"id": "m1", "caption": "Bolangiz bu samokatni ko'rsa, uyda qolmaydi\nDirektga yozing #samokat",
             "media_type": "VIDEO", "media_product_type": "REELS", "permalink": "https://www.instagram.com/reel/AAA1/",
             "timestamp": ts(5), "like_count": 400, "comments_count": 6},
            {"id": "m2", "caption": "Yangi kolleksiya", "media_type": "CAROUSEL_ALBUM",
             "media_product_type": "FEED", "permalink": "https://www.instagram.com/p/BBB2/",
             "timestamp": ts(50), "like_count": 80, "comments_count": 1},
            {"id": "m3", "caption": "", "media_type": "IMAGE", "media_product_type": "FEED",
             "permalink": "https://www.instagram.com/p/CCC3/", "timestamp": ts(200),
             "like_count": 30, "comments_count": 0},
        ]
        self.comments = {
            "m1": [
                {"id": "c1", "text": "Narxi qancha?", "username": "ona_1", "timestamp": ts(1)},
                {"id": "c2", "text": "Zo'r ekan, rahmat!", "username": "dada_2", "timestamp": ts(2)},
                {"id": "c3", "text": "Bizning do'konda arzonroq, @rival_shop ga kiring", "username": "rival", "timestamp": ts(1)},
                {"id": "c4", "text": "Suka, nima bu", "username": "troll", "timestamp": ts(1)},
                {"id": "c5", "text": "Eski komment", "username": "old", "timestamp": ts(100)},
                {"id": "c6", "text": "Qaysi yoshga mos?", "username": "ona_3", "timestamp": ts(3),
                 "replies": {"data": [{"username": "bolalar_dunyosi.uzb", "text": "3+ yosh"}]}},
            ],
            "m2": [{"id": "c7", "text": "http://spam.link", "username": "bot", "timestamp": ts(1)}],
        }

    def error(self, code, message, status=400):
        return FakeResponse({"error": {"code": code, "message": message}}, status)

    def request(self, method, url, params=None, data=None, timeout=None):
        u = urlparse(url)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        q.update({k: str(v) for k, v in (params or {}).items()})
        path = u.path.replace("/v23.0/", "").strip("/")
        self.calls.append((method, path, q.get("metric")))
        assert q.get("access_token"), "token yuborilmadi"
        if not self.token_valid:
            return self.error(190, "Error validating access token", 400)

        if method == "POST":
            if path.endswith("/replies"):
                self.replies.append((path.split("/")[0], data["message"]))
                return FakeResponse({"id": "r" + str(len(self.replies))})
            self.hidden.append(path)
            return FakeResponse({"success": True})

        if path == "me":
            return FakeResponse({"user_id": "1", "username": "bolalar_dunyosi.uzb",
                                 "followers_count": 5000, "follows_count": 10, "media_count": 3,
                                 "biography": "Bolalar o'yinchoqlari"})
        if path == "me/media":
            if q.get("after") == "P2":
                return FakeResponse({"data": self.posts[2:]})
            return FakeResponse({"data": self.posts[:2], "paging": {
                "next": "https://graph.instagram.com/v23.0/me/media?after=P2&access_token=ENV_TOKEN_1"}})
        if path == "me/stories":
            return FakeResponse({"data": []})
        if path == "me/insights":
            if q.get("metric") == "follower_demographics":
                bd = q["breakdown"]
                rows = {"city": [("Tashkent, Uzbekistan", 300), ("Samarkand, Uzbekistan", 100)],
                        "age": [("25-34", 250), ("35-44", 150)],
                        "gender": [("F", 320), ("M", 80)]}[bd]
                return FakeResponse({"data": [{"name": "follower_demographics", "total_value": {
                    "breakdowns": [{"results": [{"dimension_values": [d], "value": v} for d, v in rows]}]}}]})
            metrics = q["metric"].split(",")
            return FakeResponse({"data": [{"name": m, "total_value": {"value": 1000}} for m in metrics]})
        if path.endswith("/insights"):
            metrics = q["metric"].split(",")
            if any(m in self.INVALID for m in metrics):
                return self.error(100, "(#100) metric[0] must be one of the following values: views, reach")
            media = path.split("/")[0]
            base = {"m1": 20000, "m2": 3000, "m3": 900}[media]
            values = {"views": base, "reach": base * 0.7, "likes": base * 0.02, "comments": 6,
                      "shares": base * 0.005, "saved": base * 0.004, "total_interactions": base * 0.03,
                      "ig_reels_avg_watch_time": 6400, "profile_visits": 12, "follows": 3}
            return FakeResponse({"data": [{"name": m, "values": [{"value": values.get(m, 0)}]} for m in metrics]})
        if path.endswith("/comments"):
            media = path.split("/")[0]
            return FakeResponse({"data": self.comments.get(media, [])})
        return self.error(100, f"unknown path {path}")


class FakeAI:
    def __init__(self):
        self.prompts = []

    def generate_json(self, system, message, temperature=0.9):
        text = message.split("<komment>")[1].split("</komment>")[0].lower()
        if "narx" in text:
            return {"category": "price", "confidence": 0.9, "reply": "Direktga yozing, aytamiz. Qaysi rangi yoqdi?"}
        if "do'konda" in text:
            return {"category": "competitor", "confidence": 0.9, "reply": "Bizda kafolat bilan, tanlov sizda."}
        if "http" in text:
            return {"category": "spam", "confidence": 0.95, "reply": ""}
        if "suka" in text:
            # AI adashib "positive" desa ham, so'kinish ruxsatga ketishi kerak
            return {"category": "positive", "confidence": 0.9, "reply": "Rahmat!"}
        return {"category": "positive", "confidence": 0.9, "reply": "Rahmat, kayfiyatimiz ko'tarildi. Qaysi biri yoqdi?"}

    def generate_text(self, system, message):
        self.prompts.append((system, message))
        return "TAHLIL: " + str(len(message))


def check(cond, label):
    assert cond, label
    print("  ✅", label)


def run():
    fake = FakeInstagram()
    ig.requests.request = fake.request

    print("\n1) Instagram API va tahlil")
    data = analytics.collect(limit=3)
    check(len(data["posts"]) == 3, "3 ta post sahifalab olindi (paging ishlaydi)")
    reels = data["posts"][0]
    check(reels["views"] == 20000 and reels["avg_watch_s"] == 6.4, "Reels statistikasi to'g'ri o'qildi")
    check("ig_reels_video_view_total_time" in ig._unsupported["REELS"], "Qo'llanmaydigan ko'rsatkich eslab qolindi")
    check(abs(reels["er"] - 600 / 14000) < 1e-9, "ER = interaksiya / qamrov")
    check(reels["has_cta"] and reels["hashtags"] == 1, "Caption tahlili (CTA, hashtag)")
    check(reels["comment_samples"], "Komentlar namunasi olindi")
    check(data["audience"]["gender"][0][0] == "F", "Auditoriya demografiyasi olindi")
    summary = analytics.summary_text(data)
    check("@bolalar_dunyosi.uzb" in summary and "Reels" in summary and "Ayol 80%" in summary,
          "Raqamli hisobot matni tayyor")
    ai = FakeAI()
    result = analytics.ai_account_analysis(ai, data)
    check(result.startswith("TAHLIL") and "KONTENTLAR" in ai.prompts[-1][1], "AI tahliliga ma'lumot uzatildi")
    calls_before = len(fake.calls)
    analytics.collect(limit=3)
    bad_calls = [c for c in fake.calls[calls_before:] if c[2] and "ig_reels_video_view_total_time" in c[2]]
    check(not bad_calls, "Yaroqsiz ko'rsatkich qayta so'ralmadi")

    post = analytics.find_post("https://www.instagram.com/p/BBB2/?igsh=xyz")
    check(post and post["id"] == "m2", "Post havola bo'yicha topildi")
    post = analytics.find_post("1")
    check(post and post["id"] == "m1", "Post raqam bo'yicha topildi")
    pdata = analytics.collect_post(post)
    text = analytics.post_summary_text(pdata)
    check("barobar ko'p" in text, "Bitta post o'rtacha bilan solishtirildi")
    analytics.ai_post_analysis(ai, pdata)
    check("BARCHA KOMENTLAR" in ai.prompts[-1][1], "Post tahliliga komentlar uzatildi")

    print("\n2) Komentlarga javob — TEST rejimi")
    r = auto_reply.CommentResponder(FakeAI())
    check(r.mode == "test", "Standart rejim: test")
    events = r.run_once()
    types = {(e["type"], e["cid"]) for e in events}
    check(("test", "c1") in types and ("test", "c2") in types, "Oddiy komentlar faqat ko'rsatildi")
    check(not fake.replies, "Test rejimida hech kimga yozilmadi")
    check(("approval", "c3") in types, "Raqobatchi komment ruxsatga yuborildi")
    check(("approval", "c4") in types, "So'kingan komment ruxsatga yuborildi (AI 'positive' desa ham)")
    check(r.state.done.get("c7") == "skipped", "Spam o'tkazib yuborildi")
    check(r.state.done.get("c6") == "already", "Biz javob bergan komment qayta olinmadi")
    check(all(e["cid"] != "c5" for e in events), "Eski (24 soatdan oshgan) komment olinmadi")
    check(r.run_once() == [], "Ikkinchi tekshiruvda takror yo'q")

    print("\n3) Komentlarga javob — YONIQ rejim")
    r.set_mode("on")
    events = r.run_once()
    sent = {e["cid"] for e in events if e["type"] == "sent"}
    check(sent == {"c1", "c2"}, "Testda ko'rsatilgan oddiy komentlarga endi javob yozildi")
    check(len(fake.replies) == 2 and "Direktga" in fake.replies[0][1], "Javob Instagram'ga yuborildi")
    check(r.status()["sent_today"] == 2, "Kunlik hisoblagich ishlayapti")

    print("\n4) Egasining qarorlari")
    check(r.find_by_message(555) is None, "Bog'lanmagan xabar topilmaydi")
    r.set_message_id("c3", 555)
    check(r.find_by_message(555) == "c3", "Reply qilingan xabar kommentga bog'landi")
    msg = r.approve("c3")
    check(msg.startswith("✅") and fake.replies[-1] == ("c3", "Bizda kafolat bilan, tanlov sizda."),
          "Tasdiqlangan javob yuborildi")
    check("allaqachon" in r.approve("c3"), "Ikki marta yuborilmaydi")
    msg = r.hide("c4")
    check(msg.startswith("🙈") and fake.hidden == ["c4"], "So'kingan komment yashirildi")
    fake.comments["m1"].append({"id": "c8", "text": "Dolbayob do'kon", "username": "t2", "timestamp": ts(1)})
    r.run_once()
    check("c8" in r.state.pending, "Yangi haqoratli komment ruxsatga tushdi")
    msg = r.approve("c8", "Fikringiz uchun rahmat, sifatimizni o'zingiz ko'rib baho bering.")
    check(fake.replies[-1][0] == "c8" and "o'zingiz" in fake.replies[-1][1], "Egasining o'z matni yuborildi")

    print("\n5) Limit va xavfsizlik")
    os.environ["MAX_PER_DAY"] = "2"
    fake.comments["m1"].append({"id": "c9", "text": "Rahmat katta", "username": "u9", "timestamp": ts(1)})
    events = r.run_once()
    check(not any(e["type"] == "sent" for e in events), "Kunlik limit to'lganda yuborilmadi")
    os.environ["MAX_PER_DAY"] = "40"
    check(auto_reply.safe_reply("Narxi 250 000 so'm") == "", "Javobda narx bo'lsa yuborilmaydi")
    check(auto_reply.safe_reply("Kiring www.site.uz") == "", "Javobda link bo'lsa yuborilmaydi")
    check(auto_reply.safe_reply("Salom — rahmat 😀😀") == "Salom, rahmat 😀", "Tire va ortiqcha emoji tozalandi")
    v = auto_reply.assess({"category": "question", "confidence": 0.3, "reply": "Direktga yozing"}, "bormi?")
    check(v["action"] == "approve", "Ishonch past bo'lsa ruxsat so'raladi")
    v = auto_reply.assess(None, "???")
    check(v["action"] == "approve", "AI tushunmasa ruxsat so'raladi")
    check(not auto_reply.PROFANITY_RE.search("Sukant samokati zo'r ekan"), "Oddiy so'z haqorat deb olinmadi")
    check(auto_reply.PROFANITY_RE.search("СУКА"), "Kirill katta harfli so'kinish ham topildi")

    print("\n6) Token")
    fake.token_valid = False
    try:
        ig.get_profile()
        check(False, "190 xato ko'tarilishi kerak edi")
    except ig.InstagramError as e:
        check(e.fatal and e.code == 190, "Yaroqsiz token aniq xato beradi")
    fake.token_valid = True

    def fake_get(url, params=None, timeout=None):
        assert params["access_token"] == "ENV_TOKEN_1"
        return FakeResponse({"access_token": "REFRESHED_TOKEN", "expires_in": 5184000})

    ig.requests.get = fake_get
    check(ig.refresh_token_if_needed() is True and ig.get_token() == "REFRESHED_TOKEN", "Token yangilandi va saqlandi")
    check(ig.refresh_token_if_needed() is False, "Yangilash har kuni takrorlanmaydi")
    os.environ["INSTAGRAM_ACCESS_TOKEN"] = "ENV_TOKEN_2"
    check(ig.get_token() == "ENV_TOKEN_2", "Variables'dagi yangi token darhol ishlatiladi")
    del os.environ["INSTAGRAM_ACCESS_TOKEN"]
    ig_files = os.listdir(TMP)
    os.remove(os.path.join(TMP, "instagram_token.json"))
    check(not ig.is_configured(), "Token bo'lmasa 'ulanmagan' deb ko'rsatiladi")
    try:
        ig.get_profile()
    except ig.InstagramError as e:
        check(e.fatal and e.code == "no_token", "Token yo'q xatosi")
    os.environ["INSTAGRAM_ACCESS_TOKEN"] = "ENV_TOKEN_1"
    print("  (ma'lumot fayllari:", ", ".join(sorted(ig_files)) + ")")

    print("\n7) Telegram bot")
    import asyncio
    import telegram_runner as tr

    app = tr.build_app("123456:TEST_TOKEN_FOR_BUILD_ONLY")
    names = set()
    for handlers in app.handlers.values():
        for h in handlers:
            names.update(getattr(h, "commands", set()))
    check({"insta", "post", "stories", "autoreply", "pending", "script", "hook", "myid"} <= names,
          "Barcha buyruqlar ro'yxatdan o'tgan")
    parts = tr.split_text("a" * 5000 + "\n" + "b" * 10)
    check(all(len(p) <= tr.TELEGRAM_LIMIT for p in parts) and "".join(parts).replace("\n", "") == "a" * 5000 + "b" * 10,
          "Uzun matn bo'laklarga bo'lindi, hech narsa yo'qolmadi")
    item = {"cid": "c99", "username": "x", "text": "salom", "category": "competitor",
            "reason": "Raqobatchi komment", "reply": "Javob", "permalink": "https://i.g/p/1"}
    kb = tr.approval_keyboard(item).inline_keyboard
    check(kb[0][0].callback_data == "ar|ok|c99" and kb[0][1].callback_data == "ar|hide|c99",
          "Ruxsat tugmalari to'g'ri")
    check(len(tr.approval_keyboard({**item, "reply": ""}).inline_keyboard[0]) == 1,
          "Tayyor javob bo'lmasa 'Yuborish' tugmasi chiqmaydi")
    check("Reply qilib" in tr.approval_text(item), "Ruxsat xabari matni")

    # Egasini tekshirish
    class U:
        def __init__(self, uid):
            self.id = uid

    class Msg:
        def __init__(self):
            self.sent = []

        async def reply_text(self, text, **kw):
            self.sent.append(text)

    class Upd:
        def __init__(self, uid):
            self.effective_user = U(uid)
            self.effective_message = Msg()
            self.callback_query = None

    ran = []

    @tr.owner_only
    async def h(update, context):
        ran.append(update.effective_user.id)

    u1 = Upd(111)
    asyncio.run(h(u1, None))
    check(ran == [111] and "egasi" in u1.effective_message.sent[0], "Birinchi yozgan odam egasi bo'ldi")
    u2 = Upd(222)
    asyncio.run(h(u2, None))
    check(ran == [111] and u2.effective_message.sent == ["Bu shaxsiy bot."], "Begona odam rad etildi")
    asyncio.run(h(Upd(111), None))
    check(ran == [111, 111], "Egasi botdan foydalana oladi")

    print("\n✅ Hamma testlar o'tdi")


if __name__ == "__main__":
    run()
