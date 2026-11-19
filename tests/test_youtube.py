"""
tests/test_youtube.py
---------------------
YouTube SEO modulining testlari. Haqiqiy YouTube/Gemini kerak emas: soxta server va soxta AI.

Ishga tushirish:
    python -m tests.test_youtube
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TMP = tempfile.mkdtemp(prefix="jarvis-yt-test-")
os.environ["DATA_DIR"] = TMP
os.environ.pop("YOUTUBE_API_KEY", None)
os.environ["YT_CLIENT_ID"] = "cid"
os.environ["YT_CLIENT_SECRET"] = "secret"
os.environ["RAILWAY_PUBLIC_DOMAIN"] = "jarvis.example.app"

import youtube_api as yt  # noqa: E402
import youtube_seo as seo  # noqa: E402

GOOD_KEY = "AIza" + "x" * 35

VIDEO = {
    "id": "abcDEF12345",
    "snippet": {
        "title": "rolik krossovka",
        "description": "",
        "tags": [],
        "channelId": "UC1",
        "categoryId": "26",
        "defaultLanguage": "uz",
    },
    "contentDetails": {"duration": "PT45S"},
    "statistics": {"viewCount": "120"},
}


class FakeResponse:
    def __init__(self, body, status=200):
        self._body = body
        self.status_code = status

    def json(self):
        return self._body


calls = []


def fake_request(method, url, params=None, json=None, headers=None, timeout=None):
    params = params or {}
    calls.append((method, url, dict(params), json, dict(headers or {})))
    if url.endswith("/search"):
        return FakeResponse({"items": [{"id": {"videoId": "TOP00000001"}}, {"id": {"videoId": "TOP00000002"}}]})
    if url.endswith("/videos") and method == "GET":
        ids = params.get("id", "")
        if "TOP" in ids:
            return FakeResponse({"items": [
                {"id": "TOP00000001", "snippet": {"title": "Rolikli krossovka sharhi", "channelTitle": "A",
                                                    "tags": ["rolikli krossovka", "роликовые кроссовки"]},
                 "statistics": {"viewCount": "900000"}, "contentDetails": {"duration": "PT30S"}},
                {"id": "TOP00000002", "snippet": {"title": "Bolalar uchun rolik", "channelTitle": "B"},
                 "statistics": {"viewCount": "50000"}, "contentDetails": {"duration": "PT8M"}},
            ]})
        if ids == VIDEO["id"]:
            return FakeResponse({"items": [VIDEO]})
        if params.get("chart") == "mostPopular":
            if params.get("key") == GOOD_KEY:
                return FakeResponse({"items": [{"id": "x"}]})
            return FakeResponse({"error": {"code": 400, "message": "API key not valid",
                                           "errors": [{"reason": "badRequest"}],
                                           "details": [{"reason": "API_KEY_INVALID"}]}}, 400)
        return FakeResponse({"items": []})
    if url.endswith("/videos") and method == "PUT":
        return FakeResponse({"id": json["id"]})
    if url.endswith("/channels"):
        return FakeResponse({"items": [{"id": "UC1", "snippet": {"title": "Bolalar Dunyosi"}}]})
    raise AssertionError(f"kutilmagan so'rov: {method} {url}")


def fake_post(url, data=None, timeout=None):
    calls.append(("POST", url, dict(data or {}), None, {}))
    if data.get("grant_type") == "authorization_code":
        assert data["code"] == "CODE1"
        return FakeResponse({"access_token": "AT1", "refresh_token": "RT1", "expires_in": 3600})
    if data.get("grant_type") == "refresh_token":
        return FakeResponse({"access_token": "AT2", "expires_in": 3600})
    raise AssertionError("kutilmagan POST")


def fake_get(url, params=None, timeout=None):
    return fake_request("GET", url, params=params)


yt.requests.request = fake_request
yt.requests.get = fake_get
yt.requests.post = fake_post
yt.time.sleep = lambda s: None


class FakeAI:
    def __init__(self):
        self.last = None

    def generate_json(self, system, message, temperature=0.9):
        self.last = message
        return {
            "title": "Rolikli krossovka <bolalar uchun> | narxi va sharh",
            "alt_titles": ["Bolalar uchun rolikli krossovka", "Rolikli krossovka qanday ishlaydi?"],
            "description": "Rolikli krossovka bolalar uchun. #eski #yana\nToshkent bo'ylab yetkazib berish.",
            "hashtags": ["rolikli krossovka", "#bolalar", "#bolalar", "#rolik", "#a", "#b", "#c"],
            "tags": ["rolikli krossovka", "роликовые кроссовки", "rolik", "rolik"] + [f"teg{i} uzun ibora" for i in range(60)],
            "keywords": ["rolikli krossovka"],
            "why": "Kalit so'z boshida.",
            "tips": ["Birinchi 2 soniyada g'ildirakni ko'rsating"],
        }


def check(cond, name):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        raise SystemExit(1)


def main():
    # havolalar
    check(yt.parse_video_id("https://youtu.be/abcDEF12345?si=x") == "abcDEF12345", "youtu.be havola")
    check(yt.parse_video_id("https://www.youtube.com/watch?v=abcDEF12345&t=3") == "abcDEF12345", "watch havola")
    check(yt.parse_video_id("youtube.com/shorts/abcDEF12345 izoh") == "abcDEF12345", "shorts havola")
    check(yt.parse_video_id("salom") is None, "havolasiz matn")
    check(yt.iso_duration_sec("PT1M5S") == 65, "davomiylik")

    # kalit
    try:
        yt.set_api_key("AIza" + "y" * 35)
        check(False, "noto'g'ri kalit rad etiladi")
    except yt.YouTubeError as e:
        check("noto'g'ri" in str(e), "noto'g'ri kalit rad etiladi")
    check(yt.set_api_key(GOOD_KEY) and yt.has_key(), "to'g'ri kalit saqlanadi")

    # SEO (kanal ulanmagan)
    ai = FakeAI()
    item = seo.optimize(ai, VIDEO["id"], hint="rolikli krossovka, narxi 350 000")
    r = item["result"]
    check("<" not in r["title"] and ">" not in r["title"], "sarlavhada < > yo'q")
    check(len(r["title"]) <= 100, "sarlavha 100 belgigacha")
    check(r["hashtags"][0] == "#shorts" and len(r["hashtags"]) <= 5, "Shorts uchun #shorts, 5 tagacha hashtag")
    check(len(set(h.lower() for h in r["hashtags"])) == len(r["hashtags"]), "hashtaglar takrorlanmaydi")
    check("#eski" not in r["description"] and r["description"].endswith(" ".join(r["hashtags"])),
          "tavsifda faqat tasdiqlangan hashtaglar")
    total = sum(len(t) + (2 if " " in t else 0) for t in r["tags"]) + len(r["tags"]) - 1
    check(total <= 500, f"teglar 500 belgidan oshmaydi ({total})")
    check("Rolikli krossovka sharhi" in ai.last and "350 000" in ai.last, "AI'ga nisha va izoh berildi")
    check(seo.get_pending(item["id"]) is not None, "taklif kutish ro'yxatida")
    check("Studio" in seo.suggestion_text(item), "Telegram matni tayyor")
    check("ulanmagan" in seo.apply(item["id"]), "kanal ulanmagan bo'lsa yozilmaydi")

    # OAuth
    link = yt.auth_link()
    check("redirect_uri=https%3A%2F%2Fjarvis.example.app%2Fyoutube%2Fcallback" in link, "redirect URI to'g'ri")
    state = yt._load()["oauth_state"]
    try:
        yt.finish_oauth("CODE1", "notogri")
        check(False, "soxta state rad etiladi")
    except yt.YouTubeError:
        check(True, "soxta state rad etiladi")
    check(yt.finish_oauth("CODE1", state) == "Bolalar Dunyosi", "kanal ulandi")
    check(yt.is_connected(), "refresh token saqlandi")

    # videoga yozish
    calls.clear()
    res = seo.apply(item["id"], 1)
    put = [c for c in calls if c[0] == "PUT"]
    check(res.startswith("✅") and len(put) == 1, "videoga yozildi")
    body = put[0][3]["snippet"]
    check(body["title"] == r["alt_titles"][0], "tanlangan muqobil sarlavha yozildi")
    check(body["categoryId"] == "26" and body["defaultLanguage"] == "uz", "kategoriya va til saqlandi")
    check(put[0][4].get("Authorization") == "Bearer AT1", "OAuth token bilan yuborildi")
    check(seo.get_pending(item["id"]) is None, "taklif ro'yxatdan o'chirildi")
    check(yt.quota_used() > 0, "kvota hisoblanmoqda")
    print("\nHammasi o'tdi ✅")


if __name__ == "__main__":
    main()
