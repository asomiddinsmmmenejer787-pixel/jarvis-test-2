"""
creator_rewards/web.py
----------------------
1) Bloger kabineti (Telegram mini ilova) va uning API'si — blogerlar botining tokeni bilan imzolangan
   initData orqali tekshiriladi: bloger faqat o'z ma'lumotlarini ko'radi.
2) Jarvis HUD'dagi "Blogerlar" bo'limi API'si — faqat Jarvis egasi (asosiy bot tokeni bilan initData).
"""

import base64
import hashlib
import hmac
import os

from fastapi import APIRouter, Body, Header, HTTPException
from fastapi.responses import FileResponse, Response

from . import bot as creator_bot
from . import igconnect as ig
from . import screens
from .store import Store, StoreError

router = APIRouter(prefix="/creator")
HERE = os.path.dirname(os.path.abspath(__file__))
HUD_FILE = os.path.join(HERE, "hud", "index.html")
ADMIN_JS = os.path.join(HERE, "hud", "admin.js")
TERMS_MD = os.path.join(HERE, "terms.md")


def _store() -> Store:
    return creator_bot.store()


def _verify_creator(init_data):
    from webapp import verify_init_data  # mavjud tekshiruvni qayta ishlatamiz
    token = (os.environ.get("CREATOR_BOT_TOKEN") or "").strip()
    user = verify_init_data(init_data, token)
    if user is None:
        raise HTTPException(status_code=401, detail="Kabinetni Telegram bot orqali oching")
    return user


def _owner(init_data):
    import webapp
    return webapp.require_owner(init_data)


def _err(e):
    raise HTTPException(status_code=400, detail=str(e))


def overview_for(user, store: Store):
    store.upsert_telegram(int(user["id"]), user.get("first_name", ""), user.get("last_name", ""),
                          user.get("username", ""), user.get("photo_url"))
    data = store.overview(int(user["id"]))
    data["telegram"] = {k: user.get(k) for k in ("id", "first_name", "last_name", "username", "photo_url",
                                                 "language_code", "is_premium")}
    return data


def _decode_image(data_url):
    try:
        b64 = (data_url or "").split(",", 1)[-1]
        raw = base64.b64decode(b64, validate=False)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Rasm o'qilmadi")
    if len(raw) > screens.MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Rasm juda katta")
    return raw


# ================= bloger kabineti =================

@router.get("/app")
def hud():
    return FileResponse(HUD_FILE, headers={"Cache-Control": "no-store"})


def _md_to_html(md):
    """Shartlar matni uchun oddiy markdown: sarlavha, jadval, ro'yxat, qalin matn."""
    import html as h
    import re as _re
    out, table = [], []

    def inline(t):
        t = h.escape(t)
        return _re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)

    def flush():
        if table:
            rows = [r for r in table if not _re.match(r"^\|\s*-", r)]
            cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
            out.append("<table>" + "".join(
                "<tr>" + "".join(f"<{'th' if i == 0 else 'td'}>{inline(c)}</{'th' if i == 0 else 'td'}>" for c in row) + "</tr>"
                for i, row in enumerate(cells)) + "</table>")
            table.clear()
    for line in md.splitlines():
        if line.startswith("|"):
            table.append(line)
            continue
        flush()
        if line.startswith("# "):
            out.append(f"<h1>{inline(line[2:])}</h1>")
        elif line.startswith("## "):
            out.append(f"<h2>{inline(line[3:])}</h2>")
        elif line.startswith("> "):
            out.append(f"<blockquote>{inline(line[2:])}</blockquote>")
        elif line.strip():
            out.append(f"<p>{inline(line)}</p>")
    flush()
    return "\n".join(out)


@router.get("/terms")
def terms():
    from fastapi.responses import HTMLResponse
    with open(TERMS_MD, encoding="utf-8") as f:
        body = _md_to_html(f.read())
    page = ("<!DOCTYPE html><html lang='uz'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>Creator Rewards — Ishtirok etish shartlari</title><style>body{font:16px/1.55 system-ui,sans-serif;max-width:760px;"
            "margin:0 auto;padding:20px;color:#1F2A3C;background:#FFF8EF}h1{font-size:24px}h2{font-size:19px;margin-top:28px}"
            "table{border-collapse:collapse;width:100%;font-size:14.5px}td,th{border:1px solid #E8D9C8;padding:8px;text-align:left;"
            "vertical-align:top}th{background:#FDEBDC}blockquote{margin:0;padding:10px 14px;background:#FDEBDC;border-radius:10px}"
            "</style></head><body>" + body + "</body></html>")
    return HTMLResponse(page, headers={"Cache-Control": "no-cache"})


def _result_page(ok, title, text):
    import html as h
    from fastapi.responses import HTMLResponse
    color = "#2E7D5B" if ok else "#B3261E"
    return HTMLResponse(
        "<!DOCTYPE html><html lang='uz'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{h.escape(title)}</title></head><body style=\"font:17px/1.5 system-ui,sans-serif;background:#FFF8EF;color:#1F2A3C;"
        "display:flex;min-height:90vh;align-items:center;justify-content:center;padding:24px;text-align:center\"><div>"
        f"<div style='font-size:56px'>{'✅' if ok else '⚠️'}</div><h2 style='color:{color}'>{h.escape(title)}</h2>"
        f"<p>{h.escape(text)}</p><p style='color:#5B6475'>Bu oynani yopib, Telegram'dagi botga qayting.</p></div></body></html>",
        status_code=200 if ok else 400)


@router.get("/api/ig/link")
def ig_link(x_init_data: str = Header(default="")):
    user = _verify_creator(x_init_data)
    if not ig.configured():
        raise HTTPException(status_code=503, detail="Instagram ulash hali sozlanmagan")
    link = _store().ig_link(int(user["id"]))
    return {"url": ig.auth_url(int(user["id"])),
            "connected": bool(link and link["status"] == "ok"), "username": link["username"] if link else None}


@router.get("/ig/callback")
def ig_callback(code: str = "", state: str = "", error: str = "", error_reason: str = ""):
    """Instagram ruxsatdan keyin shu manzilga qaytaradi."""
    tg_id = ig.read_state(state)
    if tg_id is None:
        return _result_page(False, "Havola eskirgan", "Botdagi «Instagram'ni ulash» tugmasini qayta bosing.")
    if error or not code:
        return _result_page(False, "Ruxsat berilmadi", "Instagram'ni ulamasangiz ham bo'ladi — unda skrinshot yuborasiz.")
    st = _store()
    if not st.creator(tg_id):
        return _result_page(False, "Avval botda ro'yxatdan o'ting", "Botga /start yozing.")
    try:
        token, ig_user_id, expires = ig.exchange_code(code)
        me = ig.profile(token)
    except ig.IGError as e:
        return _result_page(False, "Ulab bo'lmadi", f"Instagram javobi: {e}")
    if me.get("account_type") not in ("BUSINESS", "MEDIA_CREATOR"):
        return _result_page(False, "Akkaunt professional emas",
                            "Instagram sozlamalarida akkauntni Biznes yoki Avtor turiga o'tkazib, qayta ulang.")
    try:
        st.save_ig_link(tg_id, token, me.get("user_id") or ig_user_id, me.get("username"), expires,
                        me.get("followers_count"), me.get("account_type"))
    except StoreError as e:
        return _result_page(False, "Ulab bo'lmadi", str(e))
    try:
        creator_bot.run_in_bot(creator_bot.on_ig_connected(creator_bot.bot(), tg_id), timeout=60)
    except Exception:
        pass  # ulanish saqlandi; xabar keyin ham ketishi mumkin
    return _result_page(True, f"@{me.get('username')} ulandi", "Endi ko'rishlaringiz avtomatik hisoblanadi.")


@router.get("/api/me")
def me(x_init_data: str = Header(default="")):
    return overview_for(_verify_creator(x_init_data), _store())


@router.post("/api/screenshot")
def upload_screenshot(payload: dict = Body(...), x_init_data: str = Header(default="")):
    user = _verify_creator(x_init_data)
    raw = _decode_image(payload.get("image"))
    typed = payload.get("views")
    typed = int(typed) if str(typed or "").isdigit() else None
    try:
        res = creator_bot.run_in_bot(creator_bot.submit_screenshot(
            creator_bot.bot() if creator_bot._ctx["app"] else None, int(user["id"]), int(payload.get("reel_id", 0)),
            raw, typed_views=typed))
    except (StoreError, ValueError) as e:
        _err(e)
    if res.get("need_views"):
        # Rasm saqlandi; raqamni bloger yozadi va shu rasm bilan qayta yuboriladi
        return {"need_views": True, "image_token": _sign(res["image_path"]), "image_path_id": os.path.basename(res["image_path"])}
    return {"views": res["views"], "ocr_views": res["ocr_views"], "flags": res["flags"]}


@router.post("/api/screenshot/views")
def screenshot_views(payload: dict = Body(...), x_init_data: str = Header(default="")):
    """Jarvis raqamni o'qiy olmaganda bloger raqamni yozadi (rasm qayta yuklanmaydi)."""
    user = _verify_creator(x_init_data)
    reel_id = int(payload.get("reel_id", 0))
    name = os.path.basename(str(payload.get("image_path_id", "")))
    path = os.path.join(screens.shots_dir(), str(reel_id), name)
    if not name or not os.path.isfile(path) or not hmac.compare_digest(_sign(path), str(payload.get("image_token", ""))):
        raise HTTPException(status_code=400, detail="Rasm topilmadi, qayta yuklang")
    try:
        views = int(str(payload.get("views", "")).replace(" ", ""))
        res = creator_bot.run_in_bot(creator_bot.submit_screenshot(
            creator_bot.bot(), int(user["id"]), reel_id, None, typed_views=views, image_path=path))
    except (StoreError, ValueError) as e:
        _err(e)
    return {"views": res["views"], "flags": res["flags"]}


# ================= Jarvis HUD: Blogerlar bo'limi =================

def _sign(text):
    key = ((os.environ.get("CREATOR_BOT_TOKEN") or "") + "|shots").encode()
    return hmac.new(key, str(text).encode(), hashlib.sha256).hexdigest()[:32]


def _with_image_urls(snaps):
    for s in snaps:
        if s.get("image_path"):
            s["image_url"] = f"/creator/shot/{s['id']}?sig={_sign(s['id'])}"
        s.pop("image_path", None)
    return snaps


@router.get("/admin.js")
def admin_js():
    return FileResponse(ADMIN_JS, media_type="application/javascript", headers={"Cache-Control": "no-store"})


@router.get("/shot/{snap_id}")
def shot_image(snap_id: int, sig: str = ""):
    if not hmac.compare_digest(_sign(snap_id), sig):
        raise HTTPException(status_code=403, detail="Ruxsat yo'q")
    s = _store().snapshot(snap_id)
    if not s or not s.get("image_path") or not os.path.isfile(s["image_path"]):
        raise HTTPException(status_code=404, detail="Rasm yo'q")
    with open(s["image_path"], "rb") as f:
        return Response(f.read(), media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@router.get("/admin/api/overview")
def admin_overview(x_init_data: str = Header(default="")):
    _owner(x_init_data)
    data = _store().admin_overview()
    _with_image_urls(data["screenshots"])
    data["bot_running"] = creator_bot._ctx["app"] is not None
    return data


@router.get("/admin/api/creator/{tg_id}")
def admin_creator(tg_id: int, x_init_data: str = Header(default="")):
    _owner(x_init_data)
    try:
        d = _store().creator_detail(tg_id)
    except StoreError as e:
        _err(e)
    for r in d["reels"]:
        _with_image_urls(r["snapshots"])
    return d


@router.post("/admin/api/action")
def admin_action(payload: dict = Body(...), x_init_data: str = Header(default="")):
    user = _owner(x_init_data)
    admin_id = int(user["id"])
    a = payload.get("action")
    cb = creator_bot
    try:
        if a == "brief":
            _store().set_setting("brief", str(payload.get("text", ""))[:1000], admin_id)
            return {"ok": True, "message": "Topshiriq saqlandi"}
        b = cb.bot() if cb._ctx["app"] else None
        if a == "creator":
            cb.run_in_bot(cb.act_decide_creator(b, int(payload["tg_id"]), bool(payload.get("approve")), admin_id,
                                                str(payload.get("note", ""))))
            return {"ok": True, "message": "Saqlandi"}
        if a == "verify":
            views = payload.get("views")
            cb.run_in_bot(cb.act_verify(b, int(payload["snapshot_id"]), admin_id,
                                        int(views) if views not in (None, "") else None))
            return {"ok": True, "message": "Skrinshot tasdiqlandi"}
        if a == "reward":
            v = cb.run_in_bot(cb.act_decide_reward(b, int(payload["reward_id"]), bool(payload.get("approve")), admin_id,
                                                   str(payload.get("note", ""))))
            return {"ok": True, "message": f"Vaucher chiqarildi: {v['code']}" if v else "Rad etildi"}
        if a == "redeem":
            res = cb.run_in_bot(cb.act_redeem(b, payload["code"], int(payload["amount"]), admin_id,
                                              str(payload.get("receipt", ""))))
            return {"ok": True, "message": f"Ishlatildi: {res['used']:,} so'm, qoldiq {res['left']:,} so'm".replace(",", " ")}
        if a == "promo":
            c = cb.run_in_bot(cb.act_promo(b, payload["code"], int(payload["amount"]), admin_id,
                                           str(payload.get("receipt", ""))))
            return {"ok": True, "message": f"@{c['instagram']} promo-kodi bilan xarid yozildi"}
        if a == "level":
            lv = cb.run_in_bot(cb.act_set_level(b, int(payload["tg_id"]), payload.get("level") or None, admin_id))
            return {"ok": True, "message": f"Daraja: {lv}"}
        if a == "product_request":
            cb.run_in_bot(cb.act_decide_product(b, int(payload["request_id"]), bool(payload.get("approve")), admin_id,
                                                str(payload.get("note", ""))))
            return {"ok": True, "message": "Saqlandi"}
        if a == "product_add":
            _store().add_product(str(payload.get("name", "")), admin_id)
            return {"ok": True, "message": "Mahsulot ro'yxatga qo'shildi"}
        if a == "product_active":
            _store().set_product_active(int(payload["product_id"]), bool(payload.get("active")), admin_id)
            return {"ok": True, "message": "Saqlandi"}
        if a == "vet":
            res = cb.run_in_bot(cb.run_vetting(b, int(payload["tg_id"])))
            return {"ok": True, "message": f"Baho: {res['score']}/10" if res else "Ochiq ma'lumotni olib bo'lmadi"}
        if a == "status":
            _store().set_status(int(payload["tg_id"]), payload.get("status"), admin_id)
            return {"ok": True, "message": "Holat saqlandi"}
    except (StoreError, ValueError, KeyError) as e:
        _err(e)
    raise HTTPException(status_code=400, detail="Noma'lum amal")
