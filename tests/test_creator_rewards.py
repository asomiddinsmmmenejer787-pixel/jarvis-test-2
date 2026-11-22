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


def finished_reel(s, tg, code, views):
    rid = s.add_reel(tg, f"https://www.instagram.com/reel/{code}/", posted_at=R.utcnow() - timedelta(days=31))
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
    assert R.views_to_next_step(100_000) is None


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
    s.add_reel(tg, "https://www.instagram.com/reel/AAAAA1/?igsh=xyz")
    try:
        s.add_reel(tg, "https://instagram.com/reel/AAAAA1")
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
    rid = s.add_reel(tg, "https://www.instagram.com/reel/HHHHH1/", posted_at=R.utcnow() - timedelta(days=8))
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
    rid = s.add_reel(tg, "https://www.instagram.com/reel/IIIII1/", posted_at=R.utcnow() - timedelta(days=3))
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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("  ✅", name)
    print("\n✅ Creator Rewards testlari o'tdi")
