"""
creator_rewards/web.py
----------------------
Blogerlar HUD'i (Telegram mini ilova) va uning API'si.
Har bir so'rov blogerlar botining tokeni bilan imzolangan Telegram initData orqali tekshiriladi:
bloger faqat o'z ma'lumotlarini ko'radi.
"""

import os

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import FileResponse

from . import bot as creator_bot
from .store import Store

router = APIRouter(prefix="/creator")
HUD_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hud", "index.html")


def _verify(init_data):
    from webapp import verify_init_data  # mavjud tekshiruvni qayta ishlatamiz
    token = (os.environ.get("CREATOR_BOT_TOKEN") or "").strip()
    user = verify_init_data(init_data, token)
    if user is None:
        raise HTTPException(status_code=401, detail="Kabinetni Telegram bot orqali oching")
    return user


def overview_for(user, store: Store):
    store.upsert_telegram(int(user["id"]), user.get("first_name", ""), user.get("last_name", ""),
                          user.get("username", ""), user.get("photo_url"))
    data = store.overview(int(user["id"]))
    data["telegram"] = {k: user.get(k) for k in ("id", "first_name", "last_name", "username", "photo_url",
                                                 "language_code", "is_premium")}
    return data


@router.get("/app")
def hud():
    return FileResponse(HUD_FILE, headers={"Cache-Control": "no-store"})


@router.get("/api/me")
def me(x_init_data: str = Header(default="")):
    user = _verify(x_init_data)
    return overview_for(user, creator_bot.store())
