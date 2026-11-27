"""
agency_bridge.py
----------------
AI Marketing Agentligi (alohida repo) hisobotlarini Jarvis boti orqali egasiga Telegram'da yetkazadi.

Agentlik Auditori 09:00 va 20:00 (Toshkent) da hisobotni agentlik reposidagi `outbox/*.json` ga yozadi.
Bu modul har AGENCY_POLL_SEC soniyada o'sha papkani GitHub API orqali o'qiydi va yangi fayllarni egaga yuboradi.
Jarvis'ning boshqa qismlariga tegmaydi; sozlanmagan bo'lsa hech narsa qilmaydi.

Railway Variables:
  AGENCY_REPO          - masalan "asomiddinsmmmenejer787-pixel/marketing-agency" (YOPIQ ombor bo'lishi SHART)
  AGENCY_GITHUB_TOKEN  - shu omborga "Contents: Read" ruxsatli token (bo'lmasa GITHUB_TOKEN ishlatiladi)
  AGENCY_POLL_SEC      - ixtiyoriy, standart 300
"""

import asyncio
import base64
import calendar
import json
import logging
import os
import time

import requests

import config

log = logging.getLogger("agency_bridge")
API = "https://api.github.com"
FIRST_RUN_WINDOW = 24 * 3600   # birinchi ishga tushishda faqat oxirgi 24 soatdagi hisobotlar
TG_LIMIT = 3900
status = {"enabled": False, "last_check": 0, "last_error": "", "sent": 0}


def _cfg():
    repo = os.environ.get("AGENCY_REPO", "").strip()
    token = (os.environ.get("AGENCY_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()
    return repo, token


def _state_path():
    return os.path.join(config.DATA_DIR, "agency_bridge.json")


def _load():
    try:
        with open(_state_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(st):
    os.makedirs(config.DATA_DIR, exist_ok=True)
    tmp = _state_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f)
    os.replace(tmp, _state_path())


def _gh(url, token):
    return requests.get(API + url, timeout=25, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"})


def _ts_of(name):
    """20261127T040012Z-report-morning.json -> unix vaqt (xato bo'lsa 0)."""
    try:
        return calendar.timegm(time.strptime(name[:16], "%Y%m%dT%H%M%SZ"))
    except ValueError:
        return 0


def fetch_new():
    """Yangi xabarlar ro'yxati [(nomi, matn)], holatni o'zgartirmaydi."""
    repo, token = _cfg()
    if not repo or not token:
        return None, "AGENCY_REPO yoki token o'rnatilmagan"
    r = _gh(f"/repos/{repo}", token)
    if r.status_code != 200:
        return None, f"Agentlik omborini ochib bo'lmadi ({r.status_code})"
    if not r.json().get("private"):
        return None, "Agentlik ombori ochiq (public) — mijoz ma'lumoti uchun yopiq ombor kerak, hech narsa o'qilmadi"
    r = _gh(f"/repos/{repo}/contents/outbox", token)
    if r.status_code == 404:
        return [], ""
    if r.status_code != 200:
        return None, f"outbox o'qilmadi ({r.status_code})"
    st = _load()
    seen = set(st.get("seen", []))
    first = not st.get("initialized")
    out, skipped = [], []
    for it in sorted(r.json(), key=lambda x: x.get("name", "")):
        name = it.get("name", "")
        if not name.endswith(".json") or name in seen:
            continue
        if first and time.time() - _ts_of(name) > FIRST_RUN_WINDOW:
            skipped.append(name)
            continue
        fr = _gh(f"/repos/{repo}/contents/outbox/{name}", token)
        if fr.status_code != 200:
            continue
        try:
            body = json.loads(base64.b64decode(fr.json()["content"]).decode("utf-8"))
        except (ValueError, KeyError):
            continue
        out.append((name, body.get("text") or body.get("title") or ""))
    if first:
        mark_seen(skipped)  # eski hisobotlar hech qachon yuborilmaydi
    return out, ""


def mark_seen(names, initialize=True):
    st = _load()
    seen = list(dict.fromkeys(st.get("seen", []) + list(names)))[-500:]
    st["seen"] = seen
    if initialize:
        st["initialized"] = True
    _save(st)


def _chunks(text):
    parts, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > TG_LIMIT:
            parts.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        parts.append(cur)
    return parts


async def tick(bot, owner):
    items, err = await asyncio.to_thread(fetch_new)
    status["last_check"] = time.time()
    status["last_error"] = err
    if items is None:
        return 0
    sent = []
    for name, text in items:
        try:
            for part in _chunks("🏢 Agentlik\n" + text):
                await bot.send_message(owner, part, disable_web_page_preview=True)
            sent.append(name)
        except Exception:  # noqa: BLE001
            log.exception("Agentlik hisobotini yuborib bo'lmadi: %s", name)
            break  # keyingi safar qayta urinadi
    mark_seen(sent)
    status["sent"] += len(sent)
    return len(sent)


async def loop(app, get_owner):
    repo, token = _cfg()
    if not repo or not token:
        log.info("Agentlik ko'prigi o'chiq (AGENCY_REPO o'rnatilmagan).")
        return
    status["enabled"] = True
    every = int(os.environ.get("AGENCY_POLL_SEC", "300") or 300)
    await asyncio.sleep(30)
    while True:
        try:
            owner = get_owner()
            if owner:
                await tick(app.bot, owner)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Agentlik ko'prigida xato.")
        await asyncio.sleep(max(60, every))
