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
    check(not catalog.upcoming_pending() and catalog.apply_upcoming([sam]) == [], "bir marta bajariladi")

    facts = catalog.post_facts(sam)
    check(not facts["survey"], "so'rovnoma deb adashmaydi")
    check(facts["has_price"], "narx bor")
    check(catalog.numbers_ok("Naqd narxi 7 200 000 so'm, 12 oyga oyiga 900 ming so'mdan", facts["source_text"]), "direkt raqamlari o'tadi")
    check(not catalog.numbers_ok("Narxi 6 500 000 so'm", facts["source_text"]), "to'qilgan raqam o'tmaydi")
    check(not catalog.is_dm_off("4"), "direkt yoqiq")
    print("OK: test_upcoming")


if __name__ == "__main__":
    main()
