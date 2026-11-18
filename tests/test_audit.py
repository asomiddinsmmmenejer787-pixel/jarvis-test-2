"""
tests/test_audit.py - qarorlar yozuvi va yopiq omborga yuborish.
    python -m tests.test_audit
"""
import base64
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="jarvis-audit-")

import audit  # noqa: E402
from tests.test_instagram import check  # noqa: E402


class R:
    def __init__(self, code, data=None):
        self.status_code, self._d, self.text = code, data or {}, ""

    def json(self):
        return self._d


class FakeGH:
    def __init__(self, private=True):
        self.private, self.files = private, {}

    def request(self, method, url, **kw):
        path = url.split("/contents/")[-1] if "/contents/" in url else None
        if method == "GET" and path is None:
            return R(200, {"private": self.private})
        if method == "GET":
            return R(200, {"sha": "x"}) if path in self.files else R(404)
        self.files[path] = base64.b64decode(kw["json"]["content"]).decode()
        return R(201)


def run():
    import logging
    audit.install()
    audit.record("comment", action="sent", text="Zo'r", reply="Rahmat!")
    logging.getLogger("x").error("Nimadir buzildi")
    items = audit.recent(10)
    check([i["k"] for i in items] == ["comment", "log"], "Qaror va xato yozildi")
    check(audit.ship()[1] and "o'rnatilmagan" in audit.ship(force=True)[1], "Sozlanmagan bo'lsa yuborilmaydi")

    os.environ["LOGS_REPO"], os.environ["GITHUB_TOKEN"] = "u/jarvis-logs", "t"
    gh = FakeGH(private=False)
    audit.requests.request = gh.request
    n, err = audit.ship({"mode": "on"}, force=True)
    check(n == 0 and "ochiq" in err and not gh.files, "Ochiq omborga mijoz ma'lumoti yuborilmaydi")

    gh.private = True
    audit._state["repo_checked"] = 0
    n, err = audit.ship({"mode": "on"}, force=True)
    logs = [k for k in gh.files if k.startswith("logs/")]
    check(n == 2 and not err and logs and "Rahmat!" in gh.files[logs[0]] and "status.json" in gh.files,
          "Yopiq omborga yozuvlar va holat yuborildi")
    check(audit.ship(force=True)[0] == 0, "Bir yozuv ikki marta yuborilmaydi")
    audit.record("comment", action="skip")
    check(audit.ship(force=True)[0] == 1, "Faqat yangi yozuvlar yuboriladi")
    print("\n✅ Kuzatuv testlari o'tdi")


if __name__ == "__main__":
    run()
