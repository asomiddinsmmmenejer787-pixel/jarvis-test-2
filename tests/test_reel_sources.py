"""
tests/test_reel_sources.py - obunachilar qaysi reelsdan kelayotganini taxminiy hisoblash (Top 5).
    python -m tests.test_reel_sources
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="jarvis-rsrc-")

import instagram_api as ig  # noqa: E402
import reel_sources as rs  # noqa: E402
from tests.test_instagram import check  # noqa: E402


def run():
    now = 1_800_000_000
    check(rs.top(now)["items"] == [], "Ma'lumot yo'q: bo'sh ro'yxat, xato emas")

    meta = {f"r{i}": {"caption": f"Reels {i}", "thumb": "", "link": f"https://ig/r{i}", "ts": ""} for i in range(7)}
    # 1-o'lchov
    rs.record({f"r{i}": 1000 for i in range(7)}, meta, now - 3600)
    # 2-o'lchov: r0 ko'p ko'rildi (900), r1 (100), qolganlari o'zgarmadi
    rs.record({"r0": 1900, "r1": 1100, **{f"r{i}": 1000 for i in range(2, 7)}}, None, now - 1800)
    # 3-o'lchov: r2 ko'rildi (500), r3 (500)
    rs.record({"r0": 1900, "r1": 1100, "r2": 1500, "r3": 1500, **{f"r{i}": 1000 for i in range(4, 7)}}, None, now)

    points = [[now - 3700, 100], [now - 3000, 105], [now - 2500, 104], [now - 2000, 110],  # +11 (1-oraliq)
              [now - 1000, 114], [now - 10, 118]]                                           # +8 (2-oraliq)
    out = rs.top(now, points=points)
    items = out["items"]
    check(out["gained"] == 19 and out["attributed"] == 19, "Faqat qo'shilganlar sanaladi (chiqib ketganlar emas)")
    check([x["id"] for x in items] == ["r0", "r2", "r3", "r1"], "Eng ko'p obuna olib kelgan reels birinchi")
    check(items[0]["followers"] == 10 and items[0]["views"] == 900, "Ulush ko'rishga qarab: 11 * 900/1000 ≈ 10")
    check(items[1]["followers"] == 4 and items[2]["followers"] == 4, "2-oraliq teng bo'linadi")
    check(items[0]["caption"] == "Reels 0" and items[0]["link"] == "https://ig/r0", "Reels ma'lumoti (opisaniya, havola) bor")
    check(len(items) <= 5, "Ko'pi bilan 5 ta")
    check(all(x["id"] not in ("r4", "r5", "r6") for x in items), "Ko'rilmagan reels ro'yxatga tushmaydi")

    # snapshot: tez-tez so'ralmaydi, faqat REELS olinadi
    calls = []
    orig_posts, orig_views = ig.get_recent_posts, ig.get_reel_views
    ig.get_recent_posts = lambda n=10: [
        {"id": "p1", "media_product_type": "FEED"},
        {"id": "r9", "media_product_type": "REELS", "caption": "Rolikli krasofka", "permalink": "https://ig/r9",
         "thumbnail_url": "https://cdn/r9.jpg"},
    ]
    ig.get_reel_views = lambda mid: calls.append(mid) or 777
    try:
        check(rs.snapshot(now + 60) is False and not calls, "15 daqiqa o'tmasa Instagram'ga so'rov yo'q")
        check(rs.snapshot(now + 1000) is True and calls == ["r9"], "Faqat reels ko'rishlari so'raladi")
        d = rs._load()
        check(d["meta"]["r9"]["thumb"] == "https://cdn/r9.jpg", "Muqova saqlanadi")
    finally:
        ig.get_recent_posts, ig.get_reel_views = orig_posts, orig_views

    # 23.11: ko'rishlarni o'qib bo'lmasa har daqiqada qayta so'ralmaydi (Instagram limiti to'lib,
    # komentlarga javob to'xtagan edi)
    later = now + 100000
    post_calls = []

    def no_views(mid):
        calls.append(mid)
        return None

    ig.get_recent_posts = lambda n=10: post_calls.append(n) or [
        {"id": "r10", "media_product_type": "REELS"}]
    ig.get_reel_views = no_views
    try:
        calls.clear()
        rs.snapshot(later)
        n_posts, n_views = len(post_calls), len(calls)
        check(n_posts == 1 and n_views == 1, "Birinchi urinish so'rov yuboradi")
        rs.snapshot(later + 60)
        rs.snapshot(later + 120)
        check(len(post_calls) == 1 and len(calls) == 1, "Ko'rish o'qilmasa ham 15 daqiqa kutiladi")
        rs.snapshot(later + 1000)
        check(len(post_calls) == 2, "15 daqiqadan keyin yana urinadi")

        def limited(n=10):
            post_calls.append(n)
            raise ig.InstagramError("limit", rate_limited=True)

        ig.get_recent_posts = limited
        try:
            rs.snapshot(later + 2000)
        except ig.InstagramError:
            pass
        k = len(post_calls)
        rs.snapshot(later + 2000 + 1800)
        check(len(post_calls) == k, "Limit to'lsa 1 soat so'ralmaydi")
    finally:
        ig.get_recent_posts, ig.get_reel_views = orig_posts, orig_views
        rs._next_try = 0

    # API javobida reels bloki bor
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import webapp
    from tests.test_webapp import BOT_TOKEN, OWNER, sign
    webapp.init(None, None, lambda: OWNER, BOT_TOKEN)
    orig_conf = ig.is_configured
    import followers_live
    orig_sample, orig_today = followers_live.sample, followers_live.instagram_today
    ig.is_configured = lambda: True
    followers_live.sample = lambda *a, **k: None
    followers_live.instagram_today = lambda *a, **k: None
    try:
        app = FastAPI()
        app.include_router(webapp.router)
        r = TestClient(app).get("/api/followers", headers={"X-Init-Data": sign(OWNER)}).json()
        check("reels" in r and "items" in r["reels"], "/api/followers reels Top 5 ni qaytaradi")
    finally:
        ig.is_configured = orig_conf
        followers_live.sample, followers_live.instagram_today = orig_sample, orig_today

    html = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "webapp", "index.html"),
                encoding="utf-8").read()
    check('id="rsrcList"' in html and "renderReelSources(r.reels)" in html, "HUD'da Top 5 reels bloki bor")
    check("Taxminiy" in html, "HUD hisob taxminiy ekanini aytadi")
    print("test_reel_sources: hammasi o'tdi")


if __name__ == "__main__":
    run()
