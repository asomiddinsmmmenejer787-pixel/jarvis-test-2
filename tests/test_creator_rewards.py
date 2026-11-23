"""
tests/test_creator_rewards.py
-----------------------------
Creator Rewards: formula, vaucher, qoldiq, muddat va ikki marta ishlatishdan himoya.

Ishga tushirish:
    python -m tests.test_creator_rewards
"""

import os
import sys
import tempfile
import threading
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="jarvis-cr-"))

from creator_rewards import rewards as R  # noqa: E402
from creator_rewards.store import Store, StoreError  # noqa: E402

ADMIN = 1


def new_store():
    return Store(os.path.join(tempfile.mkdtemp(prefix="cr-"), "t.db"))


def approved_creator(s, tg=100, ig="madina_blog"):
    s.upsert_telegram(tg, "Madina", "K", "madina")
    s.set_phone(tg, "+998901112233")
    s.set_instagram(tg, ig)
    s.submit_application(tg)
    s.decide_creator(tg, True, ADMIN)
    return tg


def req(s, tg, product="Rolikli krossovka"):
    """Mahsulotga ruxsat (har bir video uchun alohida so'rov)."""
    rid = s.request_product(tg, f"{product} {len(s.approved_requests(tg))}-{os.urandom(2).hex()}")
    s.decide_product_request(rid, True, ADMIN)
    return rid


def finished_reel(s, tg, code, views):
    rid = s.add_reel(tg, f"https://www.instagram.com/reel/{code}/", posted_at=R.utcnow() - timedelta(days=31), request_id=req(s, tg))
    s.close_due_reels()
    snap = s.add_snapshot(rid, views, "screenshot", file_id="f")
    return rid, s.verify_snapshot(snap, ADMIN)


def test_formula():
    cases = {0: 0, 9_999: 0, 10_000: 100_000, 10_999: 100_000, 11_000: 104_000, 30_000: 180_000,
             50_000: 260_000, 99_999: 456_000, 100_000: 460_000, 2_000_000: 460_000}
    for views, want in cases.items():
        assert R.reward_for_views(views) == want, (views, R.reward_for_views(views))
    assert R.MAX_REWARD == 460_000
    assert R.views_to_next_step(9_500) == 500
    assert R.views_to_next_step(12_300) == 700
    assert R.views_to_next_step(100_000) == 900_000  # endi maqsad — 1 000 000
    assert R.views_to_next_step(1_000_000) is None
    # 1 000 000 ko'rish: 200$ MB kursi bo'yicha, 460 000 o'rniga
    assert R.reward_for_views(1_000_000, 12_650.43) == 2_530_000
    assert R.reward_for_views(999_999, 12_650.43) == 460_000


def test_registration_rules():
    s = new_store()
    s.upsert_telegram(5, "Ali")
    try:
        s.submit_application(5)
        raise AssertionError("telefonsiz ariza o'tmasligi kerak")
    except StoreError:
        pass
    approved_creator(s, 6, "same_ig")
    s.upsert_telegram(7, "Vali")
    try:
        s.set_instagram(7, "@Same_IG")
        raise AssertionError("bitta Instagram ikki Telegramga ulanmasligi kerak")
    except StoreError:
        pass
    assert s.creator(6)["promo_code"] == "SAMEIG"


def test_reel_needs_approval_and_unique():
    s = new_store()
    s.upsert_telegram(8, "X")
    try:
        s.add_reel(8, "https://www.instagram.com/reel/AAAAA1/")
        raise AssertionError("tasdiqlanmagan bloger video qo'sha olmasligi kerak")
    except StoreError:
        pass
    tg = approved_creator(s)
    s.add_reel(tg, "https://www.instagram.com/reel/AAAAA1/?igsh=xyz", request_id=req(s, tg))
    try:
        s.add_reel(tg, "https://instagram.com/reel/AAAAA1", request_id=req(s, tg))
        raise AssertionError("bir video ikki marta qo'shilmasligi kerak")
    except StoreError:
        pass


def test_reward_requires_admin_and_is_idempotent():
    s = new_store()
    tg = approved_creator(s)
    rid, rw = finished_reel(s, tg, "BBBBB1", 30_000)
    assert rw["amount"] == 180_000 and rw["status"] == "pending"
    assert s.overview(tg)["balance"]["available"] == 0  # tasdiqsiz vaucher yo'q
    assert s.create_reward(rid)["id"] == rw["id"]  # ikkinchi hisob-kitob yaratilmaydi
    v = s.decide_reward(rw["id"], True, ADMIN)
    assert v["nominal"] == 180_000
    try:
        s.decide_reward(rw["id"], True, ADMIN)
        raise AssertionError("bitta hisob-kitobga ikki vaucher chiqmasligi kerak")
    except StoreError:
        pass


def test_million_views_uses_rate_on_approval_day():
    s = new_store()
    tg = approved_creator(s)
    s.usd_rate = lambda: 12_500.0
    _, rw = finished_reel(s, tg, "MMMMM1", 1_250_000)
    assert rw["amount"] == 2_500_000
    s.usd_rate = lambda: None
    try:
        s.decide_reward(rw["id"], True, ADMIN)
        raise AssertionError("kurs bo'lmasa 200$ vaucheri chiqmasligi kerak")
    except StoreError:
        pass
    v = s.decide_reward(rw["id"], True, ADMIN, usd_rate=12_700.0)  # tasdiqlangan kundagi kurs
    assert v["nominal"] == 2_540_000 and s.reward(rw["id"])["usd_rate"] == 12_700.0


def test_products_off_by_default():
    os.environ.pop("CREATOR_PRODUCTS", None)
    s = new_store()
    tg = approved_creator(s)
    rid = s.add_reel(tg, "https://www.instagram.com/reel/NOPROD1/")
    assert s.reel(rid)["product"] is None and s.admin_overview()["products_enabled"] is False


def test_product_permission_required():
    os.environ["CREATOR_PRODUCTS"] = "1"
    try:
        _product_permission_checks()
    finally:
        os.environ.pop("CREATOR_PRODUCTS", None)


def _product_permission_checks():
    s = new_store()
    tg = approved_creator(s)
    try:
        s.add_reel(tg, "https://www.instagram.com/reel/PPPPP1/")
        raise AssertionError("ruxsatsiz video qo'shilmasligi kerak")
    except StoreError:
        pass
    r1 = s.request_product(tg, "Dron X5")
    try:
        s.add_reel(tg, "https://www.instagram.com/reel/PPPPP1/", request_id=r1)
        raise AssertionError("tasdiqlanmagan so'rov bilan video qo'shilmasligi kerak")
    except StoreError:
        pass
    s.decide_product_request(r1, True, ADMIN)
    rid = s.add_reel(tg, "https://www.instagram.com/reel/PPPPP1/", request_id=r1)
    assert s.reel(rid)["product"] == "Dron X5" and s.product_request(r1)["status"] == "used"
    try:
        s.add_reel(tg, "https://www.instagram.com/reel/PPPPP2/", request_id=r1)
        raise AssertionError("bitta ruxsat ikki videoga ishlatilmasligi kerak")
    except StoreError:
        pass
    other = approved_creator(s, 101, "other_blog")
    r2 = req(s, other)
    try:
        s.add_reel(tg, "https://www.instagram.com/reel/PPPPP3/", request_id=r2)
        raise AssertionError("boshqa blogerning ruxsati ishlamasligi kerak")
    except StoreError:
        pass
    r3 = s.request_product(tg, "Qo'g'irchoq")
    s.decide_product_request(r3, False, ADMIN, "Hozir aksiyada emas")
    assert s.product_request(r3)["status"] == "rejected"
    assert s.creator(tg)["terms_version"] == "1.0" and s.creator(tg)["terms_accepted_at"]


def test_below_minimum_no_reward():
    s = new_store()
    tg = approved_creator(s)
    rid, rw = finished_reel(s, tg, "CCCCC1", 9_999)
    assert rw is None and s.reel(rid)["status"] == "no_reward"


def test_single_use_and_carry_to_next_voucher():
    s = new_store()
    tg = approved_creator(s)
    _, rw = finished_reel(s, tg, "DDDDD1", 50_000)
    v = s.decide_reward(rw["id"], True, ADMIN)
    res = s.redeem(v["code"], 180_000, ADMIN, "chek-1")
    assert res["left"] == 80_000
    try:
        s.redeem(v["code"], 10_000, ADMIN)
        raise AssertionError("kod ikkinchi marta ishlamasligi kerak")
    except StoreError:
        pass
    assert s.overview(tg)["balance"]["available"] == 80_000
    _, rw2 = finished_reel(s, tg, "DDDDD2", 10_000)
    v2 = s.decide_reward(rw2["id"], True, ADMIN)
    assert v2["nominal"] == 180_000 and v2["carried_in"] == 80_000
    assert s.overview(tg)["balance"]["available"] == 180_000


def test_unused_active_voucher_merges():
    s = new_store()
    tg = approved_creator(s)
    _, rw = finished_reel(s, tg, "EEEEE1", 10_000)
    v1 = s.decide_reward(rw["id"], True, ADMIN)
    _, rw2 = finished_reel(s, tg, "EEEEE2", 10_000)
    v2 = s.decide_reward(rw2["id"], True, ADMIN)
    assert v2["nominal"] == 200_000
    assert s.voucher_by_code(v1["code"])["status"] == "merged"
    try:
        s.redeem(v1["code"], 1000, ADMIN)
        raise AssertionError("birlashtirilgan eski kod ishlamasligi kerak")
    except StoreError:
        pass


def test_expired_voucher_rejected():
    s = new_store()
    tg = approved_creator(s)
    _, rw = finished_reel(s, tg, "FFFFF1", 20_000)
    v = s.decide_reward(rw["id"], True, ADMIN)
    with s.tx() as db:
        db.execute("UPDATE vouchers SET expires_at=? WHERE id=?",
                   (R.to_iso(R.utcnow() - timedelta(minutes=1)), v["id"]))
    try:
        s.redeem(v["code"], 1000, ADMIN)
        raise AssertionError("muddati o'tgan vaucher ishlamasligi kerak")
    except StoreError:
        pass
    assert s.overview(tg)["balance"]["available"] == 0


def test_concurrent_redeem_only_once():
    s = new_store()
    tg = approved_creator(s)
    _, rw = finished_reel(s, tg, "GGGGG1", 40_000)
    v = s.decide_reward(rw["id"], True, ADMIN)
    ok, fail = [], []

    def go():
        try:
            ok.append(s.redeem(v["code"], 100_000, ADMIN))
        except StoreError:
            fail.append(1)

    threads = [threading.Thread(target=go) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(ok) == 1 and len(fail) == 7


def test_pending_balance_and_reminders():
    s = new_store()
    tg = approved_creator(s)
    rid = s.add_reel(tg, "https://www.instagram.com/reel/HHHHH1/", posted_at=R.utcnow() - timedelta(days=8), request_id=req(s, tg))
    s.verify_snapshot(s.add_snapshot(rid, 25_000, "screenshot"), ADMIN)
    ov = s.overview(tg)
    assert ov["balance"]["pending"] == R.reward_for_views(25_000) == 160_000
    assert ov["reels"][0]["days_left"] in (21, 22)
    days = sorted(d for r, d in s.due_reminders() if r["id"] == rid)
    assert days == [1, 7]
    for d in days:
        s.mark_reminded(rid, d)
    assert not [1 for r, d in s.due_reminders() if r["id"] == rid]


def test_restart_keeps_state():
    path = os.path.join(tempfile.mkdtemp(prefix="cr-"), "t.db")
    s = Store(path)
    tg = approved_creator(s)
    rid = s.add_reel(tg, "https://www.instagram.com/reel/IIIII1/", posted_at=R.utcnow() - timedelta(days=3), request_id=req(s, tg))
    ends = s.reel(rid)["ends_at"]
    s2 = Store(path)  # qayta ishga tushish
    assert s2.reel(rid)["ends_at"] == ends


def _signed_init_data(token, user):
    import hashlib, hmac, json, time
    from urllib.parse import urlencode
    pairs = {"auth_date": str(int(time.time())), "user": json.dumps(user)}
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(pairs)


def test_hud_api_only_own_data():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from creator_rewards import bot as cb, web as cw
    os.environ["CREATOR_BOT_TOKEN"] = "123:TEST"
    s = new_store()
    cb._ctx["store"] = s
    approved_creator(s, 555, "own_blog")
    app = FastAPI()
    app.include_router(cw.router)
    client = TestClient(app)
    assert client.get("/creator/api/me").status_code == 401
    bad = _signed_init_data("999:OTHER", {"id": 555, "first_name": "M"})
    assert client.get("/creator/api/me", headers={"X-Init-Data": bad}).status_code == 401
    good = _signed_init_data("123:TEST", {"id": 555, "first_name": "Madina", "username": "madina"})
    data = client.get("/creator/api/me", headers={"X-Init-Data": good}).json()
    assert data["profile"]["instagram"] == "own_blog" and data["telegram"]["id"] == 555
    assert data["rules"]["max_reward"] == 460_000
    assert client.get("/creator/app").status_code == 200


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))

        class M:
            message_id = len(self.sent)
        return M()

    async def send_photo(self, chat_id, photo, caption="", **kw):
        self.sent.append((chat_id, caption))


PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100


def test_level_override_and_promo():
    s = new_store()
    tg = approved_creator(s)
    assert s.overview(tg)["level"] == "Yangi"
    s.set_level(tg, "Hamkor", ADMIN)
    ov = s.overview(tg)
    assert ov["level"] == "Hamkor" and ov["level_manual"]
    s.set_level(tg, "auto", ADMIN)
    assert s.overview(tg)["level"] == "Yangi"
    try:
        s.set_level(tg, "Boss", ADMIN)
        raise AssertionError("noto'g'ri daraja o'tmasligi kerak")
    except StoreError:
        pass
    code = s.creator(tg)["promo_code"]
    s.record_promo_sale(code.lower(), 250_000, ADMIN, "chek-9")
    s.record_promo_sale(code, 100_000, ADMIN)
    assert s.overview(tg)["promo"]["count"] == 2 and s.overview(tg)["promo"]["sum"] == 350_000


def test_expiry_reminders_once_each():
    s = new_store()
    tg = approved_creator(s)
    _, rw = finished_reel(s, tg, "JJJJJ1", 20_000)
    v = s.decide_reward(rw["id"], True, ADMIN)
    assert s.expiry_reminders() == []  # 30 kun bor
    with s.tx() as db:
        db.execute("UPDATE vouchers SET expires_at=? WHERE id=?", (R.to_iso(R.utcnow() + timedelta(days=6)), v["id"]))
    first = s.expiry_reminders()
    assert len(first) == 1 and first[0][1] == "voucher" and first[0][5] == 6
    assert s.expiry_reminders() == []  # ikkinchi marta yuborilmaydi
    with s.tx() as db:
        db.execute("UPDATE vouchers SET expires_at=? WHERE id=?", (R.to_iso(R.utcnow() + timedelta(hours=20)), v["id"]))
    again = s.expiry_reminders()
    assert len(again) == 1 and again[0][5] == 1


def test_screenshot_flow_with_jarvis_reading():
    import asyncio
    from creator_rewards import bot as cb, screens
    s = new_store()
    cb._ctx["store"] = s
    cb._ctx["admins"] = lambda: {ADMIN}
    tg = approved_creator(s)
    rid = s.add_reel(tg, "https://www.instagram.com/reel/KKKKK1/", posted_at=R.utcnow() - timedelta(days=7), request_id=req(s, tg))
    fb = FakeBot()
    orig = screens.read_insights
    try:
        screens.read_insights = lambda data: {"is_insights": True, "views": 31_500, "reach": 20_000,
                                              "posted_date": R.from_iso(s.reel(rid)["posted_at"]).astimezone(R.TASHKENT).strftime("%Y-%m-%d")}
        res = asyncio.run(cb.submit_screenshot(fb, tg, rid, PNG))
        assert res["views"] == 31_500 and res["flags"] == []
        snap = s.snapshot(res["snapshot_id"])
        assert snap["ocr_views"] == 31_500 and os.path.isfile(snap["image_path"])
        assert any("31 500" in t for _, t in fb.sent)  # administratorga ketdi
        asyncio.run(cb.act_verify(fb, res["snapshot_id"], ADMIN))
        assert s.reel(rid)["last_views"] == 31_500
        # rasmda boshqa sana va kamaygan raqam -> shubha belgilari
        screens.read_insights = lambda data: {"is_insights": True, "views": 20_000, "posted_date": "2025-01-01"}
        res2 = asyncio.run(cb.submit_screenshot(fb, tg, rid, PNG, typed_views=40_000))
        text = " ".join(res2["flags"])
        assert "farq qiladi" in text and "kam" in text and "boshqa video" in text
        # o'qib bo'lmasa — bloger raqam yozadi
        screens.read_insights = lambda data: None
        res3 = asyncio.run(cb.submit_screenshot(fb, tg, rid, PNG))
        assert res3.get("need_views")
        res4 = asyncio.run(cb.submit_screenshot(fb, tg, rid, None, typed_views=33_000, image_path=res3["image_path"]))
        assert res4["views"] == 33_000
    finally:
        screens.read_insights = orig
    try:
        screens.save_image(rid, b"not an image")
        raise AssertionError("rasm bo'lmagan fayl qabul qilinmasligi kerak")
    except ValueError:
        pass
    try:
        asyncio.run(cb.submit_screenshot(fb, 999, rid, PNG))
        raise AssertionError("boshqaning videosiga skrinshot yuborib bo'lmasligi kerak")
    except StoreError:
        pass


def test_admin_api_owner_only():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import webapp
    from creator_rewards import bot as cb, web as cw
    os.environ["CREATOR_BOT_TOKEN"] = "123:TEST"
    s = new_store()
    cb._ctx["store"] = s
    approved_creator(s, 777, "admin_view")
    webapp.init(None, None, lambda: 42, "555:MAIN")
    app = FastAPI()
    app.include_router(cw.router)
    client = TestClient(app)
    blogger = _signed_init_data("123:TEST", {"id": 777, "first_name": "B"})
    assert client.get("/creator/admin/api/overview", headers={"X-Init-Data": blogger}).status_code in (401, 403)
    stranger = _signed_init_data("555:MAIN", {"id": 43, "first_name": "X"})
    assert client.get("/creator/admin/api/overview", headers={"X-Init-Data": stranger}).status_code == 403
    owner = _signed_init_data("555:MAIN", {"id": 42, "first_name": "Asomiddin"})
    d = client.get("/creator/admin/api/overview", headers={"X-Init-Data": owner}).json()
    assert d["stats"]["creators"] == 1 and d["creators"][0]["instagram"] == "admin_view"
    r = client.post("/creator/admin/api/action", headers={"X-Init-Data": owner}, json={"action": "brief", "text": "Dronlar"})
    assert r.status_code == 200 and s.get_setting("brief") == "Dronlar"
    assert client.get("/creator/admin/api/creator/777", headers={"X-Init-Data": owner}).json()["profile"]["instagram"] == "admin_view"
    assert client.get("/creator/shot/1?sig=bad").status_code == 403
    assert client.get("/creator/admin.js").status_code == 200


def test_vetting_score():
    from datetime import datetime, timedelta as td, timezone
    from creator_rewards import vet
    now = datetime(2026, 11, 23, tzinfo=timezone.utc)
    good = {"source": "instagram_api", "followers": 30_000, "posts": [
        {"at": (now - td(days=i * 3)).isoformat(), "views": 9_000 + i * 300, "likes": 500, "comments": 40, "reel": True}
        for i in range(10)]}
    v, parts, warns = vet.score(good, now)
    assert v >= 8 and not warns, (v, warns)
    bought = {"source": "instagram_api", "followers": 80_000, "posts": [
        {"at": (now - td(days=40 + i * 10)).isoformat(), "views": 60_000, "likes": 40, "comments": 1, "reel": True}
        for i in range(6)]}
    v2, _, w2 = vet.score(bought, now)
    assert v2 < v and any("sun'iy" in w for w in w2) and any("faol emas" in w for w in w2)
    dead = {"source": "screenshot", "followers": 90_000, "posts": [{"views": 900, "reel": True} for _ in range(8)]}
    v3, _, w3 = vet.score(dead, now)
    assert v3 < 5 and any("nofaol" in w for w in w3)
    assert vet.evaluate("x", None) is None
    assert "/ 10" in vet.evaluate("madina", good)["text"]


def test_bot_product_and_terms_flow():
    import asyncio
    from creator_rewards import bot as cb
    s = new_store()
    cb._ctx["store"] = s
    cb._ctx["admins"] = lambda: {ADMIN}
    fb = FakeBot()
    tg = approved_creator(s, 300, "flow_blog")
    s.add_product("Dron X5", ADMIN)
    rid = s.request_product(tg, "Dron X5")
    asyncio.run(cb.notify_product_request(fb, rid))
    assert any("Dron X5" in t for c, t in fb.sent if c == ADMIN)
    asyncio.run(cb.act_decide_product(fb, rid, True, ADMIN))
    assert any("Ruxsat berildi" in t for c, t in fb.sent if c == tg)
    assert [r["product"] for r in s.overview(tg)["product_requests"]] == ["Dron X5"]
    res = asyncio.run(cb.run_vetting(fb, tg, {"source": "screenshot", "followers": 20_000,
                                              "posts": [{"views": 6_000, "reel": True}] * 6}))
    assert res and s.creator(tg)["vet_score"] == res["score"]


def test_terms_page_and_admin_products_api():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import webapp
    from creator_rewards import bot as cb, web as cw
    s = new_store()
    cb._ctx["store"] = s
    webapp.init(None, None, lambda: 42, "555:MAIN")
    app = FastAPI()
    app.include_router(cw.router)
    client = TestClient(app)
    page = client.get("/creator/terms")
    assert page.status_code == 200 and "Ommaviy oferta" in page.text and "200 (ikki yuz) AQSh dollari" in page.text
    assert "<table>" in page.text and "<script" not in page.text
    owner = _signed_init_data("555:MAIN", {"id": 42, "first_name": "Asomiddin"})
    h = {"X-Init-Data": owner}
    assert client.post("/creator/admin/api/action", headers=h, json={"action": "product_add", "name": "Rolikli krossovka"}).status_code == 200
    d = client.get("/creator/admin/api/overview", headers=h).json()
    assert d["products"][0]["name"] == "Rolikli krossovka" and d["product_requests"] == []
    pid = d["products"][0]["id"]
    client.post("/creator/admin/api/action", headers=h, json={"action": "product_active", "product_id": pid, "active": False})
    assert s.products(active_only=True) == []


def test_ig_state_and_link_flow():
    import asyncio
    from creator_rewards import bot as cb, igconnect as ig
    os.environ["IG_APP_SECRET"] = "secret-x"
    os.environ["CREATOR_IG_REDIRECT"] = "https://jarvis.example/creator/ig/callback"
    try:
        st = ig.make_state(555, now=1000)
        assert ig.read_state(st, now=1100) == 555
        assert ig.read_state(st, now=1000 + ig.STATE_TTL + 1) is None  # eskirgan
        assert ig.read_state(st.replace("555", "556"), now=1100) is None  # soxta
        assert "client_id=975102868186193" in ig.auth_url(555) and "instagram_business_manage_insights" in ig.auth_url(555)

        s = new_store()
        cb._ctx["store"] = s
        cb._ctx["admins"] = lambda: {ADMIN}
        tg = approved_creator(s, 900, "old_name")
        s.save_ig_link(tg, "TOKEN-1", "17841", "Real_Name", 5_184_000, 25_000, "MEDIA_CREATOR")
        assert s.creator(tg)["instagram"] == "real_name"  # tasdiqlangan username
        assert "token" not in str(s.overview(tg)["ig_link"]) and "TOKEN-1" not in str(s.overview(tg))
        s.upsert_telegram(901, "Boshqa")
        try:
            s.save_ig_link(901, "T2", "17841", "real_name", 100)
            raise AssertionError("bitta Instagram ikki Telegramga ulanmasligi kerak")
        except StoreError:
            pass

        rid = s.add_reel(tg, "https://www.instagram.com/reel/APIREEL1/", posted_at=R.utcnow() - timedelta(days=5))
        real_ts = (R.utcnow() - timedelta(days=6)).strftime("%Y-%m-%dT%H:%M:%S+0000")
        calls = {"n": 0}
        orig = (ig.find_reel, ig.media_insights, ig.refresh)
        ig.find_reel = lambda token, code: {"id": "m1", "timestamp": real_ts} if code == "APIREEL1" else None
        ig.media_insights = lambda token, mid: {"views": 52_340, "reach": 40_000}
        fb = FakeBot()
        try:
            asyncio.run(cb.sync_creator(fb, s.ig_link(tg)))
            r = s.reel(rid)
            assert r["last_views"] == 52_340
            assert R.from_iso(r["posted_at"]) < R.utcnow() - timedelta(days=5, hours=12)  # haqiqiy vaqt olindi
            assert s.snapshots_of(rid)[-1]["source"] == "instagram_api"
            # muddat tugashi: yakuniy raqam API'dan, mukofot tasdiqqa
            with s.tx() as db:
                db.execute("UPDATE reels SET ends_at=? WHERE id=?", (R.to_iso(R.utcnow() - timedelta(minutes=1)), rid))
            asyncio.run(cb.tick(fb))
            rw = s._one("SELECT * FROM rewards WHERE reel_id=?", (rid,))
            assert rw and rw["amount"] == R.reward_for_views(52_340) and rw["status"] == "pending"
            assert not any("skrinshot" in t.lower() and c == tg for c, t in fb.sent)
            # ruxsat bekor qilinsa — ulanish uzilgan deb belgilanadi va blogerga xabar ketadi
            def bad(*a):
                raise ig.IGError("Invalid OAuth access token", token_bad=True)
            ig.find_reel = bad
            s.add_reel(tg, "https://www.instagram.com/reel/APIREEL2/")
            asyncio.run(cb.sync_creator(fb, s.ig_link(tg)))
            assert s.ig_link(tg)["status"] == "broken" and any("uzildi" in t for c, t in fb.sent if c == tg)
        finally:
            ig.find_reel, ig.media_insights, ig.refresh = orig
    finally:
        os.environ.pop("IG_APP_SECRET", None)
        os.environ.pop("CREATOR_IG_REDIRECT", None)


def test_ig_callback_endpoint():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from creator_rewards import bot as cb, igconnect as ig, web as cw
    os.environ["IG_APP_SECRET"] = "secret-y"
    os.environ["CREATOR_IG_REDIRECT"] = "https://jarvis.example/creator/ig/callback"
    os.environ["CREATOR_BOT_TOKEN"] = "123:TEST"
    s = new_store()
    cb._ctx["store"] = s
    s.upsert_telegram(950, "Madina")
    app = FastAPI()
    app.include_router(cw.router)
    client = TestClient(app)
    orig = (ig.exchange_code, ig.profile)
    try:
        assert "eskirgan" in client.get("/creator/ig/callback?code=x&state=bad").text
        st = ig.make_state(950)
        assert "berilmadi" in client.get(f"/creator/ig/callback?state={st}&error=access_denied").text
        ig.exchange_code = lambda code: ("LONG", "178", 5_184_000)
        ig.profile = lambda token: {"user_id": "178", "username": "madina_blog", "account_type": "PERSONAL"}
        assert "professional emas" in client.get(f"/creator/ig/callback?code=abc%23_&state={st}").text
        ig.profile = lambda token: {"user_id": "178", "username": "madina_blog", "account_type": "MEDIA_CREATOR",
                                    "followers_count": 31_000}
        r = client.get(f"/creator/ig/callback?code=abc&state={st}")
        assert r.status_code == 200 and "@madina_blog ulandi" in r.text
        assert s.ig_link(950)["token"] == "LONG" and s.creator(950)["instagram"] == "madina_blog"
        good = _signed_init_data("123:TEST", {"id": 950, "first_name": "Madina"})
        d = client.get("/creator/api/ig/link", headers={"X-Init-Data": good}).json()
        assert d["connected"] is True and d["url"].startswith("https://www.instagram.com/oauth/authorize")
    finally:
        ig.exchange_code, ig.profile = orig
        for k in ("IG_APP_SECRET", "CREATOR_IG_REDIRECT"):
            os.environ.pop(k, None)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("  ✅", name)
    print("\n✅ Creator Rewards testlari o'tdi")
