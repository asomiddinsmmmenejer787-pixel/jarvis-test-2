"""
tests/test_hud.py - HUD bosh sahifasi: jonli lenta (/api/feed) va sahifa tuzilishi.
    python -m tests.test_hud
"""
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="jarvis-hud-")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import brain  # noqa: E402
import webapp  # noqa: E402
from tests.test_instagram import check  # noqa: E402
from tests.test_webapp import BOT_TOKEN, OWNER, sign  # noqa: E402

HTML = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "webapp", "index.html")


def run():
    webapp.init(None, None, lambda: OWNER, BOT_TOKEN)
    app = FastAPI()
    app.include_router(webapp.router)
    client = TestClient(app)

    check(client.get("/api/feed").status_code in (401, 403), "Lenta faqat egasi uchun")
    h = {"X-Init-Data": sign(OWNER)}
    check(client.get("/api/feed", headers=h).json()["items"] == [], "Bo'sh jurnal: bo'sh lenta, xato emas")
    for i in range(5):
        brain.log_entry(f"Komentga javob: @ona{i} «narxi?» → Direktga yozdim")
    items = client.get("/api/feed?n=3", headers=h).json()["items"]
    check(len(items) == 3 and "@ona4" in items[0]["x"], "Lenta yangisidan boshlanadi va n bilan cheklanadi")
    check(len(client.get("/api/feed?n=999", headers=h).json()["items"]) == 5, "Katta n xavfsiz cheklanadi")

    html = open(HTML, encoding="utf-8").read()
    views = re.findall(r'class="view[^"]*" id="v-(\w+)"', html)
    tabs = re.findall(r'<button[^>]*data-v="(\w+)"', html.split("<nav")[1].split("</nav>")[0])
    # 28.11 egasi: Nexora agentlik ofisi alohida bo'lim -> 6 ta tugma
    check(set(tabs) <= set(views) and len(tabs) <= 6, f"Pastki menyu 6 tadan oshmaydi va har biri bo'limga olib boradi {tabs}")
    for key in ("/api/feed", "/api/ads", "/api/comments", "/api/stock", "/api/posts", "/api/chat", "/api/insta"):
        check(key in html, f"HUD {key} bilan ishlaydi")
    for el in ("adsTargets", "pending", "stockList", "postList", "msgs"):
        check(f'id="{el}"' in html, f"#{el} joyida")
    print("\n✅ HUD testlari o'tdi")


if __name__ == "__main__":
    run()
