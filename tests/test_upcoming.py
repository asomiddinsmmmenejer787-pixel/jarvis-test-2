"""
tests/test_upcoming.py
----------------------
Kelayotgan post (Yadea G100 videosi) ma'lumotlari o'zi to'ldirilishi.

Ishga tushirish:
    python -m tests.test_upcoming
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="jarvis-up-")

import catalog  # noqa: E402
import auto_reply  # noqa: E402
from tests.test_instagram import check  # noqa: E402


def post(pid, ts, caption="", kind="VIDEO"):
    return {"id": pid, "timestamp": ts, "caption": caption, "media_type": kind}


def main():
    catalog.UPCOMING_ENABLED = True
    setup = catalog.UPCOMING_POSTS[0]
    old = post("1", "2026-11-21T10:00:00+0000", "Yadea samokat eski video")
    img = post("2", "2026-11-21T14:00:00+0000", "samokat rasm", kind="IMAGE")
    other = post("3", "2026-11-21T14:10:00+0000", "Rolikli krasofka")
    sam = post("4", "2026-11-21T14:20:00+0000", "Bir so'm to'lamasdan samokat. Kommentga yozing")

    check(catalog.pick_upcoming_post(setup, [old, img]) is None, "eski post va rasm tanlanmaydi")
    check(catalog.pick_upcoming_post(setup, [other, sam, old])["id"] == "4", "opisaniyasida samokat bor video tanlanadi")
    check(catalog.pick_upcoming_post(setup, [other, old])["id"] == "3", "samokat yozilmagan bo'lsa, birinchi yangi video")

    # egasi o'zi kiritgan ma'lumot ustidan yozilmaydi
    catalog.set_post_info("4", {"title": "", "price": "", "info": "", "task": ""})
    check(catalog.upcoming_pending(), "hali yozilmagan")
    done = catalog.apply_upcoming([sam, other])
    check([p["id"] for p in done] == ["4"], "bitta post yozildi")
    info = catalog.get_post_info("4")
    check("7 200 000" in info["price"], "narx yozildi")
    check("Sport Plus" in info["info"] and "50-70" in info["info"], "ma'lumot yozildi")
    check(0 < len(info["task"]) <= 500 and "bomba aksiya" in info["task"], "vazifa 500 belgidan oshmaydi")
    check(info["use_caption"] is False, "opisaniya o'chiq")
    check(catalog.flag_done(setup["flag"]) and catalog.apply_upcoming([sam]) == [], "bir marta bajariladi")

    facts = catalog.post_facts(sam)
    check(not facts["survey"], "so'rovnoma deb adashmaydi")
    check(facts["has_price"], "narx bor")
    check(catalog.numbers_ok("Naqd narxi 7 200 000 so'm, 12 oyga oyiga 900 ming so'mdan", facts["source_text"]), "direkt raqamlari o'tadi")
    check(not catalog.numbers_ok("Narxi 6 500 000 so'm", facts["source_text"]), "to'qilgan raqam o'tmaydi")
    check(not catalog.is_dm_off("4"), "direkt yoqiq")
    # 21.11 kuzatuv: G100 videosi 18:00 dan oldin joylangan, direktga "3 milliondan" deb ketgan
    early = post("5", "2026-11-21T11:30:00+0000", "Bir so'm to'lamasdan SAMOKAT oling")
    yesterday = post("6", "2026-11-20T12:00:00+0000", "Samokat eski")
    check(catalog.pick_g100_post([other, early, yesterday])["id"] == "5", "bugungi samokat videosi vaqtidan qat'i nazar topiladi")
    check(catalog.pick_g100_post([yesterday, img]) is None, "kechagi post tanlanmaydi")
    catalog.set_post_info("5", {"title": "Samokat", "price": "3 000 000 so'mdan", "info": "Rangi qora"})
    got = catalog.apply_g100_fix([other, early, yesterday], now=catalog._ts("2026-11-21T14:30:00+0000"))
    info = catalog.get_post_info("5")
    check(got["id"] == "5" and "7 200 000" in info["price"] and "3 000 000" not in info["price"],
          "eski narx ustidan G100 narxi yozildi")
    check("Rangi qora" in info["info"] and "Sport Plus" in info["info"], "egasining ma'lumoti saqlandi")
    check(catalog.get_fixed_dm("5") == catalog.G100_DM and "7 200 000" in catalog.G100_DM, "direkt matni saqlandi")
    check(catalog.apply_g100_fix([early]) is None and catalog.flag_done(catalog.G100_FIX["flag"]), "bir marta bajariladi")

    r = auto_reply.CommentResponder(None)
    r.set_dm_mode("auto")
    c = {"id": "c1", "text": "SAMOKAT", "username": "u1"}
    v = {"category": "price", "reply": "Direktdan javob berdik",
         "dm": {"text": "Samokatlarimiz 3 milliondan boshlanadi", "answered": True, "topic": "narx"}}
    plan = r._plan_dm(early, c, v, preview=True)
    check(plan and plan["text"] == catalog.G100_DM and plan["safe"], "direktga AI matni emas, egasining aniq matni ketadi")
    check("3 mln" not in plan["text"] and "3 000 000" not in plan["text"], "3 milliondan degan gap yo'q")
    check(r._plan_dm(early, c, {**v, "category": "positive"}, preview=True) is None, "maqtovga direkt yozilmaydi")
    check(r._plan_dm(other, c, v, preview=True)["text"] != catalog.G100_DM, "boshqa postga bu matn ketmaydi")
    # trial reellar: har biri o'z opisaniyasi bo'yicha, bir-biriga adashmaydi
    r1 = post("11", "2026-11-22T04:00:00+0000", "Samokat olishdan oldin buni ko'ring 👀 Yadea G100")
    r4 = post("14", "2026-11-22T10:00:00+0000", "Bu samokatning narxini taxmin qiling 🤔")
    s1 = next(s for s in catalog.UPCOMING_POSTS if s["flag"] == "setup_g100_trial1")
    s4 = next(s for s in catalog.UPCOMING_POSTS if s["flag"] == "setup_g100_trial4")
    s2 = next(s for s in catalog.UPCOMING_POSTS if s["flag"] == "setup_g100_trial2")
    check(catalog.pick_upcoming_post(s1, [r4, r1])["id"] == "11", "1-reel o'z opisaniyasi bo'yicha topiladi")
    check(catalog.pick_upcoming_post(s2, [r4, r1]) is None, "2-reel hali chiqmagan: boshqasi olinmaydi")
    done = catalog.apply_upcoming([r1, r4])
    check(sorted(p["id"] for p in done) == ["11", "14"], "chiqqan ikkala reel yozildi")
    check("taxmin" in catalog.get_post_info("14")["task"], "4-reelda taxmin vazifasi")
    check(len(catalog.get_post_info("14")["task"]) <= 500, "4-reel vazifasi 500 belgidan oshmaydi")
    check("+998 77 499 47 43" in catalog.get_post_info("11")["info"], "telefon raqami ma'lumotda")
    check(catalog.get_fixed_dm("11") == catalog.G100_DM and catalog.get_fixed_dm("14") == catalog.G100_DM,
          "trial reellarda ham aniq direkt matni")
    check(catalog.upcoming_pending(), "qolgan reellar kutilmoqda")
    print("OK: test_upcoming")


if __name__ == "__main__":
    main()
