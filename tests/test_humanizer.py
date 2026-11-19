"""
tests/test_humanizer.py
-----------------------
Javoblarni "odamday" qiluvchi filtr testlari.

Ishga tushirish:
    python -m tests.test_humanizer
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from humanizer_uz import humanize  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print("  ok:", msg)


def run():
    out = humanize("Albatta! Ajoyib savol! 32 bor — olib qo'yaymi?!! 😊😊🔥", kind="dm")
    check("Albatta" not in out and "Ajoyib savol" not in out, "robot iboralar olib tashlandi")
    check("—" not in out and "!!" not in out, "tire va qo'sh undov yo'q")
    check(sum(out.count(e) for e in "😊🔥") <= 1, "ko'pi bilan 1 emoji")

    out = humanize("Assalomu alaykum! Ha, bor. Qaysi tumandasiz?", kind="dm", already_greeted=True)
    check(out == "Ha, bor. Qaysi tumandasiz?", "davom etayotgan suhbatda salom takrorlanmaydi")

    out = humanize("Assalomu alaykum! Narxi 389 000 so'm.", kind="dm")
    check(out.startswith("Assalomu alaykum") and "389 000" in out, "birinchi xabarda salom va raqam saqlanadi")

    out = humanize("**Narxi:** 300 ming\n- 28 dan 37 gacha razmer", kind="dm")
    check("**" not in out and "\n-" not in out, "ro'yxat va qalin harf olib tashlandi")

    out = humanize("Narxi 300 ming so'm. Sizga aynan qaysi o'lchami kerak edi? Katalog kanalda bor.\n"
                   "Qaysi o'lchamni tekshirib ko'raylik?", kind="dm")
    check(out.count("?") == 1 and out.endswith("tekshirib ko'raylik?") and "\n" in out,
          "bir xabarda bir xil savol ikki marta berilmaydi")
    out = humanize("Qaysi rang kerak? Qaysi shahardasiz?", kind="dm")
    check(out.count("?") == 2, "turli savollar saqlanadi")

    long = "Bu juda yaxshi mahsulot. " * 20
    check(len(humanize(long, kind="comment")) <= 200, "komment 200 belgidan oshmaydi")
    print("\n✅ Humanizer testlari o'tdi")


if __name__ == "__main__":
    run()
