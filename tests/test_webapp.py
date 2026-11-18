"""
tests/test_webapp.py
--------------------
Mini ilova (Jarvis HUD) API testlari: Telegram imzosi, egasi tekshiruvi,
jarayon bosqichlari, tahlil, chat va komentlar.

Ishga tushirish:
    python -m tests.test_webapp
"""

import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
from urllib.parse import urlencode

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="jarvis-web-")
os.environ["INSTAGRAM_ACCESS_TOKEN"] = "ENV_TOKEN_1"
os.environ["REPLY_DELAY_MIN"] = os.environ["REPLY_DELAY_MAX"] = "0"

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import instagram_api as ig  # noqa: E402
import webapp  # noqa: E402
from auto_reply import CommentResponder  # noqa: E402
from core.jarvis import Jarvis  # noqa: E402
from tests.test_instagram import FakeAI, FakeInstagram, check  # noqa: E402

BOT_TOKEN = "123456:TEST-TOKEN"
OWNER = 777


def sign(user_id, bot_token=BOT_TOKEN, auth_date=None):
    fields = {
        "auth_date": str(auth_date or int(time.time())),
        "query_id": "AAE1",
        "user": json.dumps({"id": user_id, "first_name": "Asomiddin"}, separators=(",", ":")),
    }
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


class FakeJarvisAI(FakeAI):
    def generate_text(self, system, message):
        return "Jarvis javobi: " + message[-30:]


def wait(client, job, headers):
    for _ in range(100):
        if job["status"] != "running":
            return job
        time.sleep(0.05)
        job = client.get(f"/api/job/{job['id']}", headers=headers).json()
    raise AssertionError("jarayon tugamadi")


def run():
    fake = FakeInstagram()
    ig.requests.request = fake.request
    ai = FakeJarvisAI()
    jarvis = Jarvis(ai_provider=ai, memory_dir=os.path.join(os.environ["DATA_DIR"], "memory"))
    responder = CommentResponder(ai)
    webapp.init(jarvis, responder, lambda: OWNER, BOT_TOKEN)
    app = FastAPI()
    app.include_router(webapp.router)
    client = TestClient(app)
    H = {"X-Init-Data": sign(OWNER)}

    print("\n1) Kirish xavfsizligi")
    check(client.get("/app").status_code == 200, "Sahifa ochiladi")
    check(client.get("/api/status").status_code == 401, "Imzosiz so'rov rad etildi")
    check(client.get("/api/status", headers={"X-Init-Data": sign(OWNER, "999:OTHER")}).status_code == 401,
          "Boshqa bot imzosi rad etildi")
    check(client.get("/api/status", headers={"X-Init-Data": sign(555)}).status_code == 403,
          "Begona Telegram foydalanuvchisi rad etildi")
    old = sign(OWNER, auth_date=int(time.time()) - 3 * 86400)
    check(client.get("/api/status", headers={"X-Init-Data": old}).status_code == 401, "Eskirgan imzo rad etildi")
    tampered = sign(OWNER).replace("Asomiddin", "Boshqa")
    check(client.get("/api/status", headers={"X-Init-Data": tampered}).status_code == 401,
          "O'zgartirilgan ma'lumot rad etildi")

    print("\n2) Holat va tahlil")
    st = client.get("/api/status", headers=H).json()
    check(st["instagram"]["username"] == "bolalar_dunyosi.uzb" and st["instagram"]["followers"] == 5000,
          "HUD holati: Instagram profili")
    check(st["autoreply"]["mode"] == "test", "HUD holati: avtojavob rejimi")
    job = wait(client, client.post("/api/insta", json={"limit": 3}, headers=H).json(), H)
    check(job["status"] == "done", "Instagram tahlili jarayoni tugadi")
    check(all(s["state"] == "done" for s in job["steps"]) and len(job["steps"]) == 6, "Barcha 6 bosqich bajarildi")
    rep = job["result"]
    check(len(rep["posts"]) == 3 and rep["analysis"].startswith("Jarvis javobi"), "Hisobot va AI xulosasi bor")
    check(client.get("/api/status", headers=H).json()["report"]["created"] == rep["created"],
          "Oxirgi hisobot HUD uchun saqlandi")
    job = wait(client, client.post("/api/post", json={"ref": "1"}, headers=H).json(), H)
    check(job["status"] == "done" and "Reels" in job["result"]["summary"], "Bitta post tahlili")
    job = wait(client, client.post("/api/post", json={"ref": "99"}, headers=H).json(), H)
    check(job["status"] == "error" and "topilmadi" in job["error"], "Topilmagan post uchun tushunarli xato")

    print("\n3) Kontent (chat)")
    job = wait(client, client.post("/api/chat", json={"text": "/hook samokat"}, headers=H).json(), H)
    check(job["status"] == "done" and job["result"]["reply"], "Jarvis javob berdi")
    check([s["key"] for s in job["steps"]] == ["received", "understand", "context", "generate", "prepare"]
          and all(s["state"] == "done" for s in job["steps"]), "Chat bosqichlari ketma-ket bajarildi")
    r = client.post("/api/chat", json={"text": "IGAA" + "x" * 60}, headers=H)
    check(r.status_code == 400, "Token chat orqali AI'ga yuborilmaydi")

    print("\n4) Komentlar")
    responder.run_once()
    pend = client.get("/api/comments", headers=H).json()
    check(len(pend["pending"]) == 2, "Ruxsat kutayotgan komentlar ko'rinadi")
    cid = next(p["cid"] for p in pend["pending"] if p["category"] == "competitor")
    r = client.post(f"/api/comments/{cid}", json={"action": "send", "text": "O'z javobim"}, headers=H).json()
    check(r["message"].startswith("✅") and fake.replies[-1] == (cid, "O'z javobim"), "Egasining javobi yuborildi")
    r = client.post("/api/comments/mode", json={"mode": "on"}, headers=H).json()
    check(r["status"]["mode"] == "on", "Rejim panel orqali o'zgardi")
    check(client.post("/api/comments/mode", json={"mode": "xyz"}, headers=H).status_code == 400,
          "Noto'g'ri rejim rad etildi")

    r = client.post("/api/comments/limit", json={"limit": 500}, headers=H).json()
    check(r["status"]["limit_day"] == 500 and client.get("/api/status", headers=H).json()["autoreply"]["limit_day"] == 500,
          "Limit 500 ga o'zgardi va HUD holatida ko'rinadi")
    check(client.post("/api/comments/limit", json={"limit": "abc"}, headers=H).status_code == 400, "Noto'g'ri limit rad etildi")

    import followers_live
    now = time.time()
    for i, c in enumerate([1000, 1000, 1003, 1001, 1001]):
        followers_live.record(c, now - 300 + i * 60)
    followers_live._last_fetch[0] = time.time()
    r = client.get("/api/followers", headers=H).json()
    check(r["count"] == 1001 and [x["delta"] for x in r["changes"]][:2] == [-2, 3],
          "Obunachilar o'zgarishlari vaqti bilan ko'rinadi")
    check(r["today"]["gained"] == 3 and r["today"]["lost"] == 2, "Bugungi qo'shilgan va chiqqanlar hisoblandi")
    check(r["instagram_today"] == {"follows": 42, "unfollows": 7}, "Instagram'ning o'z hisobi olindi")

    print("\n✅ Mini ilova testlari o'tdi")


if __name__ == "__main__":
    run()
