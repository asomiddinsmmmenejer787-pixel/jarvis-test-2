"""
tests/test_agency_bridge.py - agentlik hisobotlarini Telegram'ga yetkazish.
    python -m tests.test_agency_bridge
"""
import asyncio
import base64
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="jarvis-agency-")

import agency_bridge as ab  # noqa: E402
from tests.test_instagram import check  # noqa: E402


class R:
    def __init__(self, code, data=None):
        self.status_code, self._d = code, data

    def json(self):
        return self._d


def name_at(ts, kind):
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(ts)) + f"-{kind}.json"


class FakeGH:
    def __init__(self, private=True, files=None):
        self.private, self.files, self.calls = private, files or {}, []

    def get(self, url, **kw):
        self.calls.append(url)
        if url.endswith("/contents/outbox"):
            return R(200, [{"name": n} for n in self.files])
        if "/contents/outbox/" in url:
            n = url.rsplit("/", 1)[-1]
            raw = json.dumps({"text": self.files[n]}).encode()
            return R(200, {"content": base64.b64encode(raw).decode()})
        return R(200, {"private": self.private})


class Bot:
    def __init__(self, fail=False):
        self.msgs, self.fail = [], fail

    async def send_message(self, chat, text, **kw):
        if self.fail:
            raise RuntimeError("telegram down")
        self.msgs.append((chat, text))


def run():
    now = time.time()
    old, new = name_at(now - 3 * 86400, "report-morning"), name_at(now - 600, "report-evening")

    os.environ.pop("AGENCY_REPO", None)
    items, err = ab.fetch_new()
    check(items is None and "AGENCY_REPO" in err, "sozlanmagan bo'lsa hech narsa qilmaydi")

    os.environ["AGENCY_REPO"], os.environ["AGENCY_GITHUB_TOKEN"] = "u/agency", "t"
    gh = FakeGH(private=False, files={new: "x"})
    ab.requests.get = gh.get
    items, err = ab.fetch_new()
    check(items is None and "public" in err and len(gh.calls) == 1, "ochiq omborni o'qimaydi")

    gh = FakeGH(files={old: "eski hisobot", new: "AUDITOR — kechki hisobot"})
    ab.requests.get = gh.get
    bot = Bot()
    n = asyncio.run(ab.tick(bot, 42))
    check(n == 1 and "kechki" in bot.msgs[0][1] and bot.msgs[0][0] == 42, "birinchi ishga tushishda faqat yangi hisobot yuboriladi")
    n = asyncio.run(ab.tick(bot, 42))
    check(n == 0 and len(bot.msgs) == 1, "bir hisobot ikki marta yuborilmaydi")

    third = name_at(now, "report-morning")
    gh.files[third] = "ertalabki"
    bad = Bot(fail=True)
    n = asyncio.run(ab.tick(bad, 42))
    check(n == 0, "Telegram xato bersa belgilanmaydi (keyin qayta uriniladi)")
    n = asyncio.run(ab.tick(bot, 42))
    check(n == 1 and "ertalabki" in bot.msgs[-1][1], "keyingi safar yuboriladi")

    long = "\n".join("qator " + str(i) * 50 for i in range(200))
    check(len(ab._chunks(long)) > 1 and all(len(c) <= ab.TG_LIMIT + 60 for c in ab._chunks(long)), "uzun matn bo'linadi")
    print("✅ Agentlik ko'prigi testlari o'tdi")


if __name__ == "__main__":
    run()
