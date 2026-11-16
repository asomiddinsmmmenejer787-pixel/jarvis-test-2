"""
tests/test_direkt.py
--------------------
Mahsulot katalogi va komentdan keyin direktga xabar (private reply) testlari.

Ishga tushirish:
    python -m tests.test_direkt
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="jarvis-dm-")
os.environ["INSTAGRAM_ACCESS_TOKEN"] = "ENV_TOKEN_1"
os.environ["REPLY_DELAY_MIN"] = os.environ["REPLY_DELAY_MAX"] = "0"
os.environ["AUTO_REPLY_MODE"] = "on"

import auto_reply  # noqa: E402
import catalog  # noqa: E402
import instagram_api as ig  # noqa: E402
from tests.test_instagram import FakeAI, FakeInstagram, check, ts  # noqa: E402


class DmAI(FakeAI):
    """Komentga javob + mahsulot aniqlash + direkt matni."""

    def __init__(self, dm_text=None, match=None):
        super().__init__()
        self.dm_text = dm_text
        self.match = match

    def generate_json(self, system, message, temperature=0.9):
        if system.startswith("Sen do'kon katalogidan"):
            return self.match
        if system.startswith("Sen do'kon nomidan Instagram direktda"):
            return {"text": self.dm_text} if self.dm_text else None
        return super().generate_json(system, message, temperature)


def run():
    os.environ["AUTO_REPLY_MODE"] = "on"  # test_instagram importi "test" qilib qo'yadi
    fake = FakeInstagram()
    ig.requests.request = fake.request

    print("\n1) Katalog")
    catalog.set_shop({"address": "Toshkent, Chilonzor 9", "phone": "+998 77 499 47 44", "delivery": ""})
    s10 = catalog.upsert_product({"name": "Elektr samokat S10", "price": "1 250 000 so'm",
                                  "description": "3-8 yosh", "keywords": "samokat, s10"})
    rolik = catalog.upsert_product({"name": "Rolikli krossovka", "price": "389 000 so'm",
                                    "keywords": ["rolik", "krossovka"]})
    check(len(catalog.active_products()) == 2 and catalog.load()["shop"].get("delivery") is None,
          "Mahsulotlar va do'kon ma'lumotlari saqlandi (bo'sh maydon saqlanmadi)")
    try:
        catalog.upsert_product({"name": "Narxsiz"})
        check(False, "Narxsiz mahsulot saqlanmasligi kerak edi")
    except ValueError:
        check(True, "Narxsiz mahsulot qabul qilinmadi")

    post = {"id": "m1", "caption": "Bolangiz bu samokatni ko'rsa uyda qolmaydi"}
    p, src = catalog.match_product(post, "Narxi qancha?")
    check(p and p["id"] == s10["id"] and src == "post matni", "Post matnidagi kalit so'z bo'yicha topildi")
    p, src = catalog.match_product({"id": "m9", "caption": "Yangi kolleksiya"}, "rolik necha pul?")
    check(p and p["id"] == rolik["id"] and src == "komment matni", "Komment matni bo'yicha topildi")
    p, _ = catalog.match_product({"id": "m9", "caption": "samokat va rolik"}, "narxi?")
    check(p is None, "Ikki mahsulot teng uchrasa, taxmin qilinmadi")
    catalog.link_post("m9", rolik["id"])
    p, src = catalog.match_product({"id": "m9", "caption": "samokat va rolik"}, "narxi?")
    check(p["id"] == rolik["id"] and src == "postga biriktirilgan", "Postga biriktirilgan mahsulot ustun")
    catalog.link_post("m9", None)
    p, src = catalog.match_product({"id": "m8", "caption": "Bayram sovg'asi"}, "qancha?",
                                   DmAI(match={"product_id": s10["id"], "confidence": 0.9}))
    check(p["id"] == s10["id"] and src == "AI aniqladi", "AI katalogdan aniqladi")
    p, _ = catalog.match_product({"id": "m8", "caption": "Bayram sovg'asi"}, "qancha?",
                                 DmAI(match={"product_id": s10["id"], "confidence": 0.4}))
    check(p is None, "AI ishonchi past bo'lsa mahsulot tanlanmadi")

    print("\n2) Direkt matni xavfsizligi")
    good = "Salom! Elektr samokat S10 narxi 1 250 000 so'm. Buyurtma: 77 499 47 44"
    text, how = catalog.compose_dm(DmAI(dm_text=good), s10, "narxi?")
    check(how == "AI" and "1 250 000" in text, "To'g'ri narxli AI matni qabul qilindi")
    text, how = catalog.compose_dm(DmAI(dm_text="Narxi atigi 990 000 so'm!"), s10, "narxi?")
    check(how == "shablon" and "1 250 000 so'm" in text and "990" not in text,
          "To'qilgan narx rad etildi, katalogdagi narx bilan shablon yuborildi")
    text, how = catalog.compose_dm(DmAI(dm_text="1250 ming so'm turadi"), s10, "narxi?")
    check(how == "AI", "'1250 ming' yozuvi to'g'ri narx deb tanildi")
    text, _ = catalog.compose_dm(None, None, "narxi?")
    check("Qaysi mahsulot" in text and "so'm" not in text, "Mahsulot aniqlanmasa narxsiz aniqlashtiruvchi xabar")
    check("Chilonzor" in text and "+998 77 499 47 44" in text, "Do'kon ma'lumotlari xabarga qo'shildi")

    print("\n3) Komentdan keyin direkt — TASDIQ rejimi")
    r = auto_reply.CommentResponder(DmAI(dm_text=good))
    check(r.dm_mode == "approve", "Standart direkt rejimi: tasdiq")
    events = r.run_once()
    types = [(e["type"], e["cid"]) for e in events]
    check(("sent", "c1") in types and ("dm_approval", "c1") in types, "Narx so'ragan komentga direkt tayyorlandi")
    check(not fake.dms, "Tasdiqsiz direkt yuborilmadi")
    check(("dm_approval", "c2") not in types, "Maqtov komentiga direkt yozilmadi")
    dm = next(d for d in r.get_dm_pending() if d["cid"] == "c1")
    check(dm["product"] == "Elektr samokat S10" and dm["source"] == "post matni", "Mahsulot to'g'ri aniqlangan")
    r.set_dm_message_id("c1", 42)
    check(r.find_dm_by_message(42) == "c1", "Telegram xabari direktga bog'landi")
    msg = r.approve_dm("c1")
    check(msg.startswith("📩") and fake.dms[-1] == ("c1", good), "Tasdiqlangach direkt yuborildi")
    check("allaqachon" in r.approve_dm("c1"), "Ikki marta yuborilmaydi")

    print("\n4) AVTO rejim va xatolar")
    r.set_dm_mode("auto")
    fake.comments["m1"].append({"id": "c20", "text": "Narx qancha bo'ladi?", "username": "u20", "timestamp": ts(1)})
    events = r.run_once()
    check(any(e["type"] == "dm_sent" and e["cid"] == "c20" for e in events) and fake.dms[-1][0] == "c20",
          "Avto rejimda direkt darhol yuborildi")
    fake.dm_forbidden = True
    fake.comments["m1"].append({"id": "c21", "text": "Narxi qancha", "username": "u21", "timestamp": ts(1)})
    events = r.run_once()
    err = next(e for e in events if e["cid"] == "c21" and e["type"].startswith("dm_"))
    check(err["type"] == "dm_error" and "instagram_business_manage_messages" in err["error"],
          "Ruxsat yo'q bo'lsa tushunarli xato qaytdi")
    fake.dm_forbidden = False
    r.set_dm_mode("off")
    fake.comments["m1"].append({"id": "c22", "text": "Narxi?", "username": "u22", "timestamp": ts(1)})
    events = r.run_once()
    check(not any(e["type"].startswith("dm") for e in events), "O'chiq rejimda direkt yozilmadi")

    print("\n5) HUD API")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import webapp
    from core.jarvis import Jarvis
    from tests.test_webapp import BOT_TOKEN, OWNER, sign
    webapp.init(Jarvis(ai_provider=DmAI(), memory_dir=os.environ["DATA_DIR"] + "/m"), r, lambda: OWNER, BOT_TOKEN)
    app = FastAPI()
    app.include_router(webapp.router)
    c = TestClient(app)
    H = {"X-Init-Data": sign(OWNER)}
    d = c.get("/api/catalog", headers=H).json()
    check(len(d["products"]) == 2 and d["posts"] and d["posts"][0]["auto"] == s10["id"],
          "Katalog va postlar (avto aniqlash bilan) ko'rinadi")
    pid = c.post("/api/catalog/product", json={"name": "Velosiped", "price": "900 000 so'm"}, headers=H).json()["product"]["id"]
    check(c.post("/api/catalog/link", json={"media_id": "m2", "product_id": pid}, headers=H).json()["ok"],
          "Post mahsulotga biriktirildi")
    check(c.post("/api/catalog/product", json={"name": "X"}, headers=H).status_code == 400, "Noto'g'ri mahsulot rad etildi")
    check(c.delete(f"/api/catalog/product/{pid}", headers=H).json()["ok"] and
          "m2" not in catalog.load()["post_links"], "O'chirilgan mahsulot bog'lanishlari ham tozalandi")
    check(c.post("/api/dm/mode", json={"mode": "approve"}, headers=H).json()["status"]["dm_mode"] == "approve",
          "Direkt rejimi HUD orqali o'zgardi")
    check(c.get("/api/catalog").status_code == 401, "Katalog API imzosiz yopiq")

    print("\n✅ Direkt testlari o'tdi")


if __name__ == "__main__":
    run()
