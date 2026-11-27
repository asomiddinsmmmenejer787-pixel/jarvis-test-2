"""Nexora ofisi: /api/nexora agentlik serveridan holatni olib beradi; server o'chiq bo'lsa xato emas, holat.
Ishga tushirish: python -m tests.test_nexora"""
import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
from urllib.parse import urlencode

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="jarvis-nx-")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import webapp  # noqa: E402


def init_data(token, uid):
    pairs = {"auth_date": str(int(time.time())), "user": json.dumps({"id": uid})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(pairs)


class Resp:
    def __init__(self, d):
        self.d = d

    def raise_for_status(self):
        pass

    def json(self):
        return self.d


def run():
    webapp.init(None, None, lambda: 42, "1:abc")
    app = FastAPI()
    app.include_router(webapp.router)
    c = TestClient(app)
    h = {"X-Init-Data": init_data("1:abc", 42)}
    seen = []
    orig = requests.get
    try:
        def ok(url, timeout=0):
            seen.append(url)
            return Resp({"online": True, "busy": False, "agents": [{"id": "auditor", "state": "idle"}], "links": []})
        requests.get = ok
        r = c.get("/api/nexora", headers=h).json()
        assert r["connected"] is True and r["agents"][0]["id"] == "auditor", r
        assert seen[0].startswith("http://marketing-agency.railway.internal:8080")
        c.get("/api/nexora", headers=h)
        assert len(seen) == 1, "2 soniyalik kesh ishlashi kerak"
        print("  ✅ agentlik holati olinadi (kesh bilan)")

        def bad(url, timeout=0):
            raise requests.ConnectionError("no route")
        requests.get = bad
        webapp._nexora.update(t=0, v=None)
        r = c.get("/api/nexora", headers=h).json()
        assert r["connected"] is False and "ulanib" in r["error"], r
        print("  ✅ agentlik serveri o'chiq bo'lsa HUD xato o'rniga holat ko'rsatadi")
        assert c.get("/api/nexora").status_code in (401, 403)
        print("  ✅ faqat egasi ko'radi")
    finally:
        requests.get = orig
    print("✅ Nexora testlari o'tdi")


if __name__ == "__main__":
    run()
