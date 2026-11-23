"""
creator_rewards/bot.py
----------------------
Blogerlar uchun alohida Telegram bot (CREATOR_BOT_TOKEN). Jarvis'ning shaxsiy botiga tegmaydi.

Bloger:  ro'yxatdan o'tish (telefon + Instagram), Reels qo'shish, skrinshot yuborish (Jarvis raqamni o'zi
         o'qiydi), vaucher, kabinet (mini ilova), administrator bilan yozishish.
Admin:   arizani, skrinshotni va mukofotni tasdiqlash; /ishlat KOD SUMMA [chek]; /promo KOD SUMMA [chek];
         /daraja @instagram Hamkor|Faol|Yangi|avto; /brif matn; /tekshir KOD; /arizalar.
         Xuddi shu amallar Jarvis HUD'ning "Blogerlar" bo'limida ham bor.
"""

import asyncio
import io
import json
import logging
import os

from telegram import (InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, MenuButtonWebApp,
                      ReplyKeyboardMarkup, ReplyKeyboardRemove, Update, WebAppInfo)
from telegram.ext import (ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes,
                          MessageHandler, filters)

from . import fx
from . import igconnect as ig
from . import rewards as R
from . import screens
from . import vet
from .store import LEVELS, Store, StoreError, products_enabled

log = logging.getLogger("creator_bot")
TICK_SEC = int(os.environ.get("CREATOR_TICK_SEC", "600") or 600)

BTN_HUD = "📱 Kabinet"
BTN_REEL = "➕ Reels qo'shish"
BTN_SHOT = "📸 Skrinshot yuborish"
BTN_VOUCHER = "🎟 Vaucherim"
BTN_CHAT = "✉️ Administratorga yozish"
BTN_PRODUCT = "🧸 Mahsulot so'rash"
TERMS_VERSION = "1.0"

RULES_TEXT = (
    "Asosiy shartlar (to'liq matn — «Shartlarni o'qish» tugmasida):\n"
    "• 18 yoshdan katta bo'lishingiz, Instagram akkauntingiz professional va ochiq bo'lishi kerak.\n"
    "• Instagram akkauntni bot orqali ulash majburiy (parolingiz bizga kelmaydi, faqat statistika o'qiladi).\n"
    "• Qaysi mahsulot suratga olinishini Bolalar Dunyosi hal qiladi va do'kon xodimi bilan kelishiladi.\n"
    "• Video do'konda olinadi, 30–45 soniya, 9:16, reklama sifatida belgilanadi.\n"
    "• Reels Bolalar Dunyosi bilan Collab qilib joylanadi, havola 24 soat ichida botga yuboriladi, video o'chirilmaydi.\n"
    "• Mukofot 30 kun ichidagi ko'rishlar uchun: 10 000 ko'rish = 100 000 so'm, keyin har 1 000 ko'rishga +4 000 so'm, "
    "100 000 dan 460 000 so'm; 1 000 000 va undan ko'p ko'rish — 200$ (MB kursi bo'yicha so'mda).\n"
    "• Mukofot faqat vaucher: naqd pulga almashtirilmaydi, 30 kun amal qiladi, kod bir marta ishlatiladi.\n"
    "• Sun'iy ko'rish, soxta skrinshot yoki brend obro'siga zarar — hisob to'xtatiladi va vaucherlar bekor qilinadi."
)

_ctx = {"store": None, "admins": lambda: set(), "app": None, "loop": None}


def store() -> Store:
    if _ctx["store"] is None:
        _ctx["store"] = Store()
    return _ctx["store"]


def admins():
    return {int(a) for a in _ctx["admins"]() if a}


def is_admin(user_id):
    return int(user_id) in admins()


def hud_url():
    url = (os.environ.get("CREATOR_WEBAPP_URL") or "").strip()
    if url:
        return url
    domain = (os.environ.get("RAILWAY_PUBLIC_DOMAIN") or "").strip()
    return f"https://{domain}/creator/app" if domain else ""


def terms_url():
    url = hud_url()
    return url.rsplit("/app", 1)[0] + "/terms" if url else ""


def consent_keyboard():
    rows = []
    if terms_url():
        rows.append([InlineKeyboardButton("📄 To'liq shartlarni o'qish", url=terms_url())])
    rows.append([InlineKeyboardButton("✅ Shartlarga roziman — boshlash", callback_data="cr|agree")])
    return InlineKeyboardMarkup(rows)


def ig_required():
    """Instagram ulanishi majburiy (ulash sozlangan bo'lsa)."""
    return ig.configured()


def ig_ok(tg_id):
    link = store().ig_link(tg_id)
    return bool(link and link["status"] == "ok")


def has_consent(c):
    return bool(c and c.get("terms_accepted_at") and c.get("terms_version") == TERMS_VERSION)


async def finish_registration(b, tg_id):
    """Instagram aniqlangach: rozilik berilgan bo'lsa ariza darhol yuboriladi."""
    c = store().creator(tg_id)
    if not has_consent(c):
        await safe_send(b, tg_id, f"Instagram: @{c['instagram']}\n\n{RULES_TEXT}", reply_markup=terms_keyboard())
        return
    await send_application(b, tg_id)


async def send_application(b, tg_id):
    c = store().submit_application(tg_id, TERMS_VERSION)
    await safe_send(b, tg_id, f"Instagram: @{c['instagram']}\n\n✅ Arizangiz yuborildi. Administrator tasdiqlagach xabar beraman.")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Tasdiqlash", callback_data=f"cr|ca|{tg_id}"),
                                InlineKeyboardButton("❌ Rad etish", callback_data=f"cr|cx|{tg_id}")]])
    rep = (json.loads(c["vet_report"]) if c.get("vet_report") else None) or {}
    await notify_admins(b, f"🆕 Yangi ariza\n{c['first_name']} {c['last_name']} (@{c['username']})\n"
                           f"Tel: {c['phone']}\nInstagram: https://instagram.com/{c['instagram']}\n"
                           f"Shartlar qabul qilindi: v{TERMS_VERSION}\n\n"
                           + (rep.get("text") or "📊 Avtomatik baho yo'q — profilni o'zingiz ko'ring"),
                        reply_markup=kb)
    return c


def terms_keyboard():
    rows = []
    if terms_url():
        rows.append([InlineKeyboardButton("📄 Shartlarni o'qish", url=terms_url())])
    rows.append([InlineKeyboardButton("✅ Shartlarni o'qidim va qabul qilaman", callback_data="cr|apply")])
    return InlineKeyboardMarkup(rows)


def run_in_bot(coro, timeout=90):
    """Web-server oqimidan bot amalini bajarish (masalan, HUD'dan tasdiqlash)."""
    loop, app = _ctx["loop"], _ctx["app"]
    if loop is None or app is None:
        coro.close()
        raise StoreError("Blogerlar boti ishlamayapti (CREATOR_BOT_TOKEN).")
    return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout)


def bot():
    return _ctx["app"].bot


def main_keyboard():
    url = hud_url()
    # Pastki klaviaturadagi web_app tugmasi Telegram ma'lumotini (initData) bermaydi — Kabinet namuna rejimida
    # ochilib qolardi. Shuning uchun oddiy tugma: bosilganda ichki "Kabinetni ochish" tugmasi yuboriladi.
    first = [KeyboardButton(BTN_HUD)] if url else []
    if products_enabled():
        rows = [first + [KeyboardButton(BTN_VOUCHER)], [KeyboardButton(BTN_PRODUCT), KeyboardButton(BTN_REEL)],
                [KeyboardButton(BTN_CHAT)]]
    else:
        rows = [first + [KeyboardButton(BTN_VOUCHER)], [KeyboardButton(BTN_REEL), KeyboardButton(BTN_CHAT)]]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


async def safe_send(b, chat_id, text, **kw):
    try:
        return await b.send_message(chat_id, text, **kw)
    except Exception:
        log.exception("Xabar yuborilmadi | chat=%s", chat_id)
        return None


async def notify_admins(b, text, **kw):
    sent = []
    for a in admins():
        m = await safe_send(b, a, text, **kw)
        if m:
            sent.append(m)
    return sent


def fmt(n):
    return f"{int(n):,}".replace(",", " ")


# ================= umumiy amallar (bot va HUD uchun) =================

async def act_decide_creator(b, tg_id, approve, admin_id, note=""):
    c = store().decide_creator(tg_id, approve, admin_id, note)
    if approve:
        await safe_send(b, tg_id, "🎉 Arizangiz tasdiqlandi! Endi do'konga kelib video olishingiz mumkin.\n"
                                  f"Obunachilaringiz uchun promo-kod: {c['promo_code']}", reply_markup=main_keyboard())
    else:
        await safe_send(b, tg_id, "Afsuski, arizangiz hozircha tasdiqlanmadi."
                        + (f"\nSabab: {note}" if note else "") + "\nSavollar bo'lsa, shu yerga yozing.")
    return c


async def submit_screenshot(b, tg_id, reel_id, data, typed_views=None, image_path=None):
    """Skrinshotni saqlaydi, Jarvis o'qiydi, tekshiradi va administratorga yuboradi.
    Raqamni o'qib bo'lmasa va bloger yozmagan bo'lsa: {'need_views': True, 'image_path': ...}."""
    st = store()
    reel = st.reel(int(reel_id))
    if not reel or reel["tg_id"] != int(tg_id):
        raise StoreError("Video topilmadi.")
    if reel["status"] not in ("monitoring", "awaiting_final"):
        raise StoreError("Bu video bo'yicha skrinshot qabul qilinmaydi.")
    if image_path is None:
        image_path = screens.save_image(reel["id"], bytes(data))
    with open(image_path, "rb") as f:
        data = f.read()
    ocr = await asyncio.to_thread(screens.read_insights, data)
    ocr_views = ocr.get("views") if isinstance(ocr, dict) and isinstance(ocr.get("views"), int) else None
    if ocr_views is None and typed_views is None:
        return {"need_views": True, "image_path": image_path}
    views = ocr_views if ocr_views is not None else int(typed_views)
    flags = screens.check_flags(reel, st.snapshots_of(reel["id"]), ocr, typed_views)
    sid = st.add_snapshot(reel["id"], views, "screenshot", actor=tg_id, image_path=image_path,
                          ocr_views=ocr_views, typed_views=typed_views, flags=flags)
    c = st.creator(tg_id)
    age = (R.utcnow() - R.from_iso(reel["posted_at"])).days
    caption = (f"📸 @{c['instagram']} — {reel['shortcode']} ({age}-kun)\n{reel['url']}\n"
               f"Jarvis o'qidi: {fmt(ocr_views) if ocr_views is not None else '—'}"
               + (f"\nBloger yozdi: {fmt(typed_views)}" if typed_views is not None else "")
               + (f"\nTaxminiy mukofot: {R.som(R.reward_for_views(views))}")
               + ("".join(f"\n⚠️ {x}" for x in flags) if flags else "\n✅ Shubhali belgi topilmadi"))
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"✅ {fmt(views)} — to'g'ri", callback_data=f"cr|sv|{sid}"),
                                InlineKeyboardButton("✏️ Boshqa raqam", callback_data=f"cr|se|{sid}")]])
    for a in admins():
        try:
            await b.send_photo(a, io.BytesIO(data), caption=caption[:1020], reply_markup=kb)
        except Exception:
            log.exception("Skrinshot administratorga yuborilmadi")
    return {"snapshot_id": sid, "views": views, "ocr_views": ocr_views, "flags": flags}


async def act_verify(b, snap_id, admin_id, views=None):
    st = store()
    reward = st.verify_snapshot(snap_id, admin_id, views)
    snap = st.snapshot(snap_id)
    reel = st.reel(snap["reel_id"])
    await safe_send(b, reel["tg_id"], f"✅ Skrinshot tasdiqlandi: {fmt(reel['last_views'])} ko'rish.\n"
                                      f"Hozirgi taxminiy mukofot: {R.som(R.reward_for_views(reel['last_views']))}")
    if reward:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Vaucher chiqarish", callback_data=f"cr|ra|{reward['id']}"),
                                    InlineKeyboardButton("❌ Rad etish", callback_data=f"cr|rx|{reward['id']}")]])
        c = st.creator(reel["tg_id"])
        await notify_admins(b, f"💰 Yakuniy hisob-kitob\n@{c['instagram']} — {reel['url']}\n"
                               f"30 kunlik ko'rishlar: {fmt(reward['views'])}\nMukofot: {R.som(reward['amount'])}",
                            reply_markup=kb)
    elif st.reel(reel["id"])["status"] == "no_reward":
        await safe_send(b, reel["tg_id"], "30 kun yakunlandi. Afsuski, 10 000 ko'rishga yetmadi, bu video uchun mukofot yo'q.")
    return reward


async def act_decide_reward(b, reward_id, approve, admin_id, note=""):
    st = store()
    rw = st.reward(int(reward_id))
    if not rw:
        raise StoreError("Hisob-kitob topilmadi.")
    reel = st.reel(rw["reel_id"])
    rate = None
    if approve and rw["views"] >= R.MILLION_VIEWS:
        rate, _ = await asyncio.to_thread(fx.usd_rate, True)
    v = st.decide_reward(int(reward_id), approve, admin_id, note, usd_rate=rate)
    if v:
        extra = f"\n(Oldingi qoldiq {R.som(v['carried_in'])} ham qo'shildi.)" if v["carried_in"] else ""
        await safe_send(b, reel["tg_id"], f"🎟 Yangi vaucher!\nKod: {v['code']}\nQiymati: {R.som(v['nominal'])}{extra}\n"
                                          f"Amal qiladi: {R.tashkent_str(v['expires_at'])} gacha.\n"
                                          "Kod bir marta ishlatiladi, qoldiq keyingi vaucherga o'tadi.")
    else:
        await safe_send(b, reel["tg_id"], f"Video bo'yicha mukofot tasdiqlanmadi: {reel['url']}"
                        + (f"\nSabab: {note}" if note else ""))
    return v


async def act_redeem(b, code, amount, admin_id, receipt=""):
    res = store().redeem(code, amount, admin_id, receipt)
    left = (f"Qoldiq: {R.som(res['left'])} — keyingi vaucheringizga qo'shiladi "
            f"({R.tashkent_str(res['left_expires_at'], False)} gacha amal qiladi)." if res["left"] else "Qoldiq yo'q.")
    await safe_send(b, res["tg_id"], f"🛍 Vaucheringiz ishlatildi.\nVaucher: {R.som(res['nominal'])}\n"
                                     f"Ishlatildi: {R.som(res['used'])}\n{left}")
    return res


async def act_promo(b, code, amount, admin_id, receipt=""):
    c = store().record_promo_sale(code, amount, admin_id, receipt)
    stats = store().promo_stats(c["tg_id"])
    await safe_send(b, c["tg_id"], f"🛒 Promo-kodingiz ({c['promo_code']}) bilan xarid bo'ldi: {R.som(int(amount))}.\n"
                                   f"Jami: {stats['count']} ta xarid.")
    return c


async def act_decide_product(b, req_id, approve, admin_id, note=""):
    req = store().decide_product_request(int(req_id), approve, admin_id, note)
    if approve:
        await safe_send(b, req["tg_id"], f"✅ Ruxsat berildi: {req['product']}\nDo'konga kelib suratga olishingiz mumkin. "
                                         f"Videoni joylagach, «{BTN_REEL}» orqali havolasini yuboring.")
    else:
        await safe_send(b, req["tg_id"], f"❌ {req['product']} uchun ruxsat berilmadi." + (f"\nSabab: {note}" if note else ""))
    return req


async def notify_product_request(b, req_id):
    st = store()
    req = st.product_request(req_id)
    c = st.creator(req["tg_id"])
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Ruxsat berish", callback_data=f"cr|pa|{req_id}"),
                                InlineKeyboardButton("❌ Rad etish", callback_data=f"cr|px|{req_id}")]])
    await notify_admins(b, f"🧸 Mahsulot so'rovi: @{c['instagram']}\n{req['product']}", reply_markup=kb)


async def run_vetting(b, tg_id, metrics=None):
    """Akkauntni baholaydi va natijani saqlaydi. Ochiq ma'lumot bo'lmasa None qaytaradi."""
    st = store()
    c = st.creator(tg_id)
    if metrics is None:
        metrics = await asyncio.to_thread(vet.fetch_business_discovery, c["instagram"])
    result = vet.evaluate(c["instagram"], metrics)
    if result:
        st.set_vetting(tg_id, result["score"], result)
    return result


async def act_set_level(b, tg_id, level, admin_id):
    st = store()
    st.set_level(tg_id, level, admin_id)
    ov = st.overview(tg_id)
    if ov["level"] == "Hamkor":
        await safe_send(b, tg_id, "⭐️ Darajangiz: Hamkor. Endi uyda suratga olish va mahsulot yetkazib berish imkoniyati ochiq.")
    else:
        await safe_send(b, tg_id, f"Darajangiz yangilandi: {ov['level']}.")
    return ov["level"]


# ================= bloger =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    c = store().upsert_telegram(u.id, u.first_name, u.last_name, u.username)
    context.user_data.clear()
    if is_admin(u.id):
        await update.message.reply_text(
            "Administrator rejimi. Hammasi Jarvis HUD'dagi «Blogerlar» bo'limida ham bor.\n"
            "/arizalar — kutilayotgan arizalar\n/ishlat KOD SUMMA [chek] — vaucherni ishlatish\n"
            "/promo KOD SUMMA [chek] — promo-kod bilan xarid\n/daraja @instagram Hamkor|Faol|Yangi|avto\n"
            "/brif matn — blogerlar uchun joriy topshiriq\n/tekshir KOD — vaucher holati\n"
            "/mahsulot nomi — ruxsat ro'yxatiga mahsulot qo'shish\n/baho @instagram — akkauntni qayta baholash\n"
            "Blogerga javob berish uchun uning xabariga Reply qiling.")
        return
    if c["status"] == "approved":
        await update.message.reply_text(f"Xush kelibsiz, {u.first_name}! Kabinetingiz tayyor.", reply_markup=main_keyboard())
    elif c["status"] == "pending":
        await update.message.reply_text("Arizangiz ko'rib chiqilmoqda. Tasdiqlangach shu yerga xabar keladi.")
    elif c["status"] == "blocked":
        await update.message.reply_text("Hisobingiz to'xtatilgan. Savollar bo'lsa, shu yerga yozing.")
    elif not has_consent(c):
        await update.message.reply_text(
            f"Assalomu alaykum, {u.first_name}! Bolalar Dunyosi Creator Rewards dasturiga xush kelibsiz.\n\n"
            f"{RULES_TEXT}\n\nBotdan foydalanish uchun shartlarga roziligingiz kerak. «Roziman» tugmasini bosish "
            "ommaviy ofertani to'liq qabul qilish hisoblanadi.", reply_markup=consent_keyboard())
    else:
        await ask_phone(update.message)


async def ask_phone(msg):
    kb = ReplyKeyboardMarkup([[KeyboardButton("📞 Telefon raqamni yuborish", request_contact=True)]],
                             resize_keyboard=True, one_time_keyboard=True)
    await msg.reply_text("Ro'yxatdan o'tish uchun telefon raqamingizni yuboring.", reply_markup=kb)


async def on_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u, contact = update.effective_user, update.message.contact
    if contact.user_id != u.id:
        await update.message.reply_text("Iltimos, o'zingizning raqamingizni tugma orqali yuboring.")
        return
    if not has_consent(store().creator(u.id)) and not is_admin(u.id):
        await update.message.reply_text("Avval shartlarga rozilik bering: /start")
        return
    store().set_phone(u.id, contact.phone_number)
    context.user_data["await"] = "instagram"
    if ig.configured():
        await update.message.reply_text("Rahmat!", reply_markup=ReplyKeyboardRemove())
        await update.message.reply_text(
            "Endi Instagram akkauntingizni ulang. Instagram o'zi ruxsat so'raydi — parolingiz bizga kelmaydi, "
            "ruxsatni istalgan vaqtda Instagram sozlamalaridan bekor qilasiz. Ulangan akkauntda ko'rishlar "
            "avtomatik hisoblanadi, skrinshot yuborish shart emas.\n\nDasturda qatnashish uchun Instagram'ni ulash majburiy.",
            reply_markup=ig_keyboard(u.id))
    else:
        await update.message.reply_text("Rahmat! Endi Instagram username'ingizni yozing (masalan: @madina_blog).",
                                        reply_markup=ReplyKeyboardRemove())


def ig_keyboard(tg_id):
    # Kabinet ichida ochiladi: Telegram'dan chiqmaydi, Instagram ilovasi ruxsat oynasini buzmaydi
    url = hud_url()
    if url:
        return InlineKeyboardMarkup([[InlineKeyboardButton(
            "🔗 Instagram'ni ulash", web_app=WebAppInfo(url=url + ("&" if "?" in url else "?") + "ig=1"))]])
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔗 Instagram'ni ulash", url=ig.auth_url(tg_id))]])


async def on_ig_connected(b, tg_id):
    """Web callback'dan keyin: akkauntni baholaydi va blogerga keyingi qadamni yuboradi."""
    st = store()
    link = st.ig_link(tg_id)
    try:
        metrics = await asyncio.to_thread(ig.account_metrics, link["token"])
    except ig.IGError:
        metrics = None
    if metrics:
        await run_vetting(b, tg_id, metrics)
    c = st.creator(tg_id)
    await safe_send(b, tg_id, f"✅ Instagram ulandi: @{link['username']}")
    if c["status"] in ("draft", "rejected"):
        await finish_registration(b, tg_id)
    elif c["status"] == "approved":
        await safe_send(b, tg_id, "Endi videolaringiz statistikasi avtomatik olinadi — skrinshot yuborish shart emas.",
                        reply_markup=main_keyboard())
    await notify_admins(b, f"🔗 @{link['username']} Instagram'ini ulab berdi.")
    return c


async def sync_creator(b, link):
    """Ulangan blogerning kuzatuvdagi videolari ko'rishlarini Instagram'dan oladi."""
    st = store()
    token = link["token"]
    try:
        left = (R.from_iso(link["expires_at"]) - R.utcnow()).days
        if left < 15:
            token, exp = await asyncio.to_thread(ig.refresh, token)
            st.update_ig_token(link["tg_id"], token, exp)
        reels = [r for r in st.reels_of(link["tg_id"]) if r["status"] in ("monitoring", "awaiting_final")]
        for r in reels:
            m = await asyncio.to_thread(ig.find_reel, token, r["shortcode"])
            if not m:
                continue
            ins = await asyncio.to_thread(ig.media_insights, token, m["id"])
            if ins.get("views") is not None:
                st.apply_api_views(r["id"], ins["views"], m.get("timestamp"),
                                   extra={k: v for k, v in ins.items() if k != "views"})
        st.mark_ig(link["tg_id"], synced=True)
    except ig.IGError as e:
        if e.token_bad:
            st.mark_ig(link["tg_id"], status="broken")
            await safe_send(b, link["tg_id"], "⚠️ Instagram ulanishi uzildi (ruxsat bekor qilingan yoki muddati o'tgan). "
                                              "Qayta ulang, aks holda skrinshot yuborishingiz kerak bo'ladi.",
                            reply_markup=ig_keyboard(link["tg_id"]) if ig.configured() else None)
        else:
            log.warning("Instagram sinxronlashda xato | tg=%s: %s", link["tg_id"], e)


async def finalize_linked(b, reel):
    """30 kun tugagan, Instagram'i ulangan blogerning videosi: yakuniy raqam API'dan, mukofot tasdiqqa."""
    st = store()
    reward = st.create_reward(reel["id"])
    if reward:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Vaucher chiqarish", callback_data=f"cr|ra|{reward['id']}"),
                                    InlineKeyboardButton("❌ Rad etish", callback_data=f"cr|rx|{reward['id']}")]])
        c = st.creator(reel["tg_id"])
        await notify_admins(b, f"💰 Yakuniy hisob-kitob (Instagram'dan avtomatik)\n@{c['instagram']} — {reel['url']}\n"
                               f"30 kunlik ko'rishlar: {fmt(reward['views'])}\nMukofot: {R.som(reward['amount'])}",
                            reply_markup=kb)
        await safe_send(b, reel["tg_id"], f"⏱ 30 kun yakunlandi: {fmt(reward['views'])} ko'rish. Mukofot administrator tasdig'ida.")
    else:
        await safe_send(b, reel["tg_id"], "30 kun yakunlandi. Afsuski, 10 000 ko'rishga yetmadi, bu video uchun mukofot yo'q.")


async def voucher_text(tg_id):
    ov = store().overview(tg_id)
    b = ov["balance"]
    lines = [f"Asosiy balans: {R.som(b['available'])}", f"Kutilayotgan (30 kun ichida): {R.som(b['pending'])}"]
    v = ov["voucher"]
    if v:
        lines.append(f"\nFaol vaucher: {v['code']}\nQiymati: {R.som(v['nominal'])}\n"
                     f"Amal qiladi: {R.tashkent_str(v['expires_at'])} gacha")
    if ov["carry"]:
        lines.append(f"\nQoldiq: {R.som(ov['carry']['amount'])} — keyingi vaucheringizga qo'shiladi "
                     f"({R.tashkent_str(ov['carry']['expires_at'], False)} gacha).")
    if ov["profile"]["promo_code"]:
        lines.append(f"\nPromo-kodingiz: {ov['profile']['promo_code']} · {ov['promo']['count']} ta xarid")
    return "\n".join(lines)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u, text = update.effective_user, (update.message.text or "").strip()
    st = store()
    c = st.upsert_telegram(u.id, u.first_name, u.last_name, u.username)
    if is_admin(u.id):
        await admin_text(update, context, text)
        return
    state = context.user_data.get("await") or ""
    try:
        if state == "instagram" and ig_required():
            await update.message.reply_text("Instagram faqat tugma orqali ulanadi — username yozish o'rniga shu tugmani bosing.",
                                            reply_markup=ig_keyboard(u.id))
            return
        if state == "instagram":
            igname = st.set_instagram(u.id, text)
            context.user_data.pop("await", None)
            await update.message.reply_text(f"🔎 @{igname} akkauntini tekshiryapman...")
            result = await run_vetting(context.bot, u.id)
            if result is None:
                context.user_data["await"] = "profile_shot"
                await update.message.reply_text(
                    "Instagram profilingizning Reels bo'limidan skrinshot yuboring: obunachilar soni va videolar "
                    "ustidagi ko'rishlar ko'rinib tursin.")
                return
            await finish_registration(context.bot, u.id)
            return
        if state == "product":
            req_id = st.request_product(u.id, text)
            context.user_data.pop("await", None)
            await update.message.reply_text("So'rovingiz yuborildi. Ruxsat berilsa, shu yerga xabar keladi.",
                                            reply_markup=main_keyboard())
            await notify_product_request(context.bot, req_id)
            return
        if state == "reel" or state.startswith("reel:"):
            rid = st.add_reel(u.id, text, request_id=int(state.split(":")[1]) if ":" in state else None)
            context.user_data.pop("await", None)
            r = st.reel(rid)
            await update.message.reply_text(
                f"✅ Video ro'yxatga olindi.\nKuzatuv {R.tashkent_str(r['ends_at'])} gacha davom etadi.\n"
                "1, 7, 14 va 30-kunlarda Insights skrinshotini so'rayman.", reply_markup=main_keyboard())
            await notify_admins(context.bot, f"🎬 Yangi Reels: @{c['instagram']}"
                                             + (f" — {r['product']}" if r.get("product") else "") + f"\n{r['url']}")
            return
        if state.startswith("views:"):
            _, rid, path = state.split(":", 2)
            views = int(text.replace(" ", "").replace(",", "").replace(".", ""))
            context.user_data.pop("await", None)
            await submit_screenshot(context.bot, u.id, int(rid), None, typed_views=views, image_path=path)
            await update.message.reply_text("Rahmat! Skrinshot administratorga yuborildi.", reply_markup=main_keyboard())
            return
    except StoreError as e:
        await update.message.reply_text(str(e))
        return
    except ValueError:
        await update.message.reply_text("Faqat raqam yozing, masalan: 25400")
        return

    if c["status"] != "approved":
        await start(update, context)
        return
    if text == BTN_PRODUCT and products_enabled():
        products = st.products(active_only=True)
        kb = [[InlineKeyboardButton(p["name"], callback_data=f"cr|preq|{p['id']}")] for p in products[:20]]
        kb.append([InlineKeyboardButton("✏️ Boshqa mahsulot (yozib yuborish)", callback_data="cr|pother")])
        brief = st.get_setting("brief", "")
        await update.message.reply_text(("📝 Joriy topshiriq: " + brief + "\n\n" if brief else "")
                                        + "Qaysi mahsulotni suratga olmoqchisiz? Ruxsatni Bolalar Dunyosi beradi.",
                                        reply_markup=InlineKeyboardMarkup(kb))
    elif text == BTN_REEL and ig_required() and not ig_ok(u.id):
        await update.message.reply_text("Video qo'shish uchun Instagram ulangan bo'lishi kerak. Ulanish uzilgan — qayta ulang.",
                                        reply_markup=ig_keyboard(u.id))
    elif text == BTN_REEL and not products_enabled():
        context.user_data["await"] = "reel"
        brief = st.get_setting("brief", "")
        await update.message.reply_text(("📝 Joriy topshiriq: " + brief + "\n\n" if brief else "")
                                        + "Instagram'da joylagan Collab Reels havolasini yuboring.")
    elif text == BTN_REEL:
        approved = st.approved_requests(u.id)
        if not approved:
            await update.message.reply_text(f"Avval mahsulotga ruxsat oling: «{BTN_PRODUCT}».")
        elif len(approved) == 1:
            context.user_data["await"] = f"reel:{approved[0]['id']}"
            await update.message.reply_text(f"Mahsulot: {approved[0]['product']}\nInstagram'da joylagan Collab Reels havolasini yuboring.")
        else:
            kb = [[InlineKeyboardButton(a["product"], callback_data=f"cr|rq|{a['id']}")] for a in approved[:10]]
            await update.message.reply_text("Videoda qaysi mahsulot bor?", reply_markup=InlineKeyboardMarkup(kb))
    elif text == BTN_SHOT:
        reels = [r for r in st.reels_of(u.id) if r["status"] in ("monitoring", "awaiting_final")]
        if not reels:
            await update.message.reply_text("Hozir kuzatuvdagi video yo'q.")
            return
        kb = [[InlineKeyboardButton(f"{r['shortcode']} · {R.tashkent_str(r['posted_at'], False)}",
                                    callback_data=f"cr|shot|{r['id']}")] for r in reels[:10]]
        await update.message.reply_text("Qaysi video uchun?", reply_markup=InlineKeyboardMarkup(kb))
    elif text == BTN_HUD:
        url = hud_url()
        await update.message.reply_text("Kabinetingiz:", reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("📱 Kabinetni ochish", web_app=WebAppInfo(url=url))]]) if url else None)
    elif text == BTN_VOUCHER:
        await update.message.reply_text(await voucher_text(u.id))
    elif text == BTN_CHAT:
        await update.message.reply_text("Xabaringizni yozing — administratorga yetkazaman.")
    else:
        sent = await notify_admins(context.bot, f"✉️ @{c['instagram'] or u.username} ({c['first_name']}):\n{text}\n\n"
                                                "Javob berish uchun shu xabarga Reply qiling.")
        for m in sent:
            st.remember_relay(m.message_id, u.id)
        await update.message.reply_text("Xabaringiz administratorga yuborildi.")


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    state = context.user_data.get("await") or ""
    if state == "profile_shot":
        f = await update.message.photo[-1].get_file()
        data = bytes(await f.download_as_bytearray())
        await update.message.reply_text("🔎 Jarvis profilingizni o'qiyapti...")
        metrics = await asyncio.to_thread(vet.read_profile_screenshot, data)
        await run_vetting(context.bot, u.id, metrics or {"source": "screenshot", "followers": None, "posts": []})
        context.user_data.pop("await", None)
        await finish_registration(context.bot, u.id)
        return
    if not state.startswith("photo:"):
        await update.message.reply_text("Skrinshotni Kabinet'dagi «Videolar» bo'limidan yoki bot so'raganda "
                                        "xabar ostidagi tugma orqali yuboring.")
        return
    rid = int(state.split(":")[1])
    f = await update.message.photo[-1].get_file()
    data = await f.download_as_bytearray()
    await update.message.reply_text("🔎 Jarvis skrinshotni o'qiyapti...")
    try:
        res = await submit_screenshot(context.bot, u.id, rid, data)
    except (StoreError, ValueError) as e:
        await update.message.reply_text(str(e))
        return
    if res.get("need_views"):
        context.user_data["await"] = f"views:{rid}:{res['image_path']}"
        await update.message.reply_text("Raqamni aniq o'qiy olmadim. Skrinshotdagi ko'rishlar (Views) sonini yozing:")
        return
    context.user_data.pop("await", None)
    await update.message.reply_text(f"✅ Jarvis o'qidi: {fmt(res['views'])} ko'rish. Administratorga yuborildi.",
                                    reply_markup=main_keyboard())


# ================= tugmalar =================

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split("|")
    action, arg = parts[1], (parts[2] if len(parts) > 2 else "")
    u = q.from_user
    st = store()
    try:
        if action == "agree":
            st.accept_terms(u.id, TERMS_VERSION)
            await q.answer("Rahmat!")
            await q.edit_message_reply_markup(None)
            c = st.creator(u.id)
            if c["status"] in ("draft", "rejected") and not c["phone"]:
                await ask_phone(q.message)
            else:
                await q.message.reply_text("✅ Rozilik qabul qilindi.", reply_markup=main_keyboard() if c["status"] == "approved" else None)
            return
        if action == "apply":
            st.accept_terms(u.id, TERMS_VERSION)
            await q.answer("Ariza yuborildi")
            await q.edit_message_reply_markup(None)
            await send_application(context.bot, u.id)
            return
        if action == "preq":
            p = next((x for x in st.products(active_only=True) if x["id"] == int(arg)), None)
            if not p:
                await q.answer("Bu mahsulot endi ro'yxatda yo'q", show_alert=True)
                return
            req_id = st.request_product(u.id, p["name"])
            await q.answer("So'rov yuborildi")
            await q.edit_message_text(f"🧸 {p['name']} — so'rov yuborildi. Ruxsat berilsa, xabar keladi.")
            await notify_product_request(context.bot, req_id)
            return
        if action == "pother":
            context.user_data["await"] = "product"
            await q.answer()
            await q.message.reply_text("Mahsulot nomini yozing (masalan: rolikli krossovka, qizil, 32-o'lcham):")
            return
        if action == "rq":
            req = st.product_request(int(arg))
            if not req or req["tg_id"] != u.id or req["status"] != "approved":
                await q.answer("Ruxsat topilmadi", show_alert=True)
                return
            context.user_data["await"] = f"reel:{req['id']}"
            await q.answer()
            await q.message.reply_text(f"Mahsulot: {req['product']}\nInstagram'da joylagan Collab Reels havolasini yuboring.")
            return
        if action == "shot":
            context.user_data["await"] = f"photo:{int(arg)}"
            await q.answer()
            await q.message.reply_text("Shu videoning Insights ekranidan skrinshot yuboring (Views ko'rinib tursin).")
            return
        if not is_admin(u.id):
            await q.answer("Ruxsat yo'q")
            return
        if action in ("ca", "cx"):
            await act_decide_creator(context.bot, int(arg), action == "ca", u.id)
            await q.answer("Saqlandi")
            await q.edit_message_text(q.message.text + ("\n\n✅ Tasdiqlandi" if action == "ca" else "\n\n❌ Rad etildi"))
        elif action == "sv":
            await act_verify(context.bot, int(arg), u.id)
            await q.answer("Tasdiqlandi")
            await q.edit_message_caption((q.message.caption or "") + "\n\n✅ Tasdiqlandi")
        elif action == "se":
            context.user_data["await"] = f"fix:{int(arg)}"
            await q.answer()
            await q.message.reply_text("Skrinshotdagi to'g'ri ko'rishlar sonini yozing:")
        elif action in ("pa", "px"):
            await act_decide_product(context.bot, int(arg), action == "pa", u.id)
            await q.answer("Saqlandi")
            await q.edit_message_text(q.message.text + ("\n\n✅ Ruxsat berildi" if action == "pa" else "\n\n❌ Rad etildi"))
        elif action in ("ra", "rx"):
            v = await act_decide_reward(context.bot, int(arg), action == "ra", u.id)
            await q.answer("Saqlandi")
            await q.edit_message_text(q.message.text + (f"\n\n✅ Vaucher chiqarildi: {v['code']}" if v else "\n\n❌ Rad etildi"))
    except StoreError as e:
        await q.answer(str(e)[:190], show_alert=True)


# ================= administrator =================

async def admin_text(update, context, text):
    st = store()
    state = context.user_data.get("await") or ""
    replied = update.message.reply_to_message
    try:
        if state.startswith("fix:"):
            views = int(text.replace(" ", "").replace(",", ""))
            context.user_data.pop("await", None)
            await act_verify(context.bot, int(state.split(":")[1]), update.effective_user.id, views)
            await update.message.reply_text("Saqlandi.")
            return
        if replied is not None:
            target = st.relay_target(replied.message_id)
            if target:
                await context.bot.send_message(target, f"✉️ Administrator:\n{text}")
                await update.message.reply_text("Yuborildi.")
                return
    except StoreError as e:
        await update.message.reply_text(str(e))
        return
    except ValueError:
        await update.message.reply_text("Faqat raqam yozing.")
        return
    await update.message.reply_text("Buyruqlar: /arizalar, /ishlat, /promo, /daraja, /brif, /mahsulot, /baho, /tekshir")


def admin_only(fn):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if is_admin(update.effective_user.id):
            try:
                await fn(update, context)
            except (StoreError, ValueError) as e:
                await update.message.reply_text(f"❌ {e}")
    return wrapper


@admin_only
async def cmd_redeem(update, context):
    a = context.args
    if len(a) < 2:
        await update.message.reply_text("Ishlatish: /ishlat BD-XXXX-XXXX 180000 [chek raqami]")
        return
    res = await act_redeem(context.bot, a[0], int(a[1].replace(" ", "")), update.effective_user.id, " ".join(a[2:]))
    await update.message.reply_text(f"✅ {res['code']}: {R.som(res['used'])} ishlatildi, qoldiq {R.som(res['left'])}.")


@admin_only
async def cmd_promo(update, context):
    a = context.args
    if len(a) < 2:
        await update.message.reply_text("Ishlatish: /promo MADINABL 250000 [chek raqami]")
        return
    c = await act_promo(context.bot, a[0], int(a[1].replace(" ", "")), update.effective_user.id, " ".join(a[2:]))
    await update.message.reply_text(f"✅ @{c['instagram']} promo-kodi bilan xarid yozildi.")


@admin_only
async def cmd_level(update, context):
    a = context.args
    if len(a) < 2:
        await update.message.reply_text("Ishlatish: /daraja @instagram Hamkor | Faol | Yangi | avto")
        return
    c = store().creator_by_instagram(a[0])
    if not c:
        await update.message.reply_text("Bunday Instagram bilan bloger topilmadi.")
        return
    level = a[1].capitalize() if a[1].lower() not in ("avto", "auto") else None
    lv = await act_set_level(context.bot, c["tg_id"], level, update.effective_user.id)
    await update.message.reply_text(f"✅ @{c['instagram']} darajasi: {lv}" + (" (qo'lda)" if level else " (avtomatik)"))


async def cmd_connect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bloger: Instagram'ni ulash havolasi."""
    if not ig.configured():
        await update.message.reply_text("Instagram ulash hozircha mavjud emas — skrinshot orqali davom etamiz.")
        return
    store().upsert_telegram(update.effective_user.id, update.effective_user.first_name)
    await update.message.reply_text("Instagram'ni ulash uchun tugmani bosing. Parolingiz bizga kelmaydi.",
                                    reply_markup=ig_keyboard(update.effective_user.id))


@admin_only
async def cmd_product(update, context):
    """/mahsulot Rolikli krossovka — ruxsat ro'yxatiga qo'shadi; /mahsulot (bo'sh) — ro'yxat."""
    name = " ".join(context.args).strip()
    if name:
        store().add_product(name, update.effective_user.id)
        await update.message.reply_text(f"✅ Ro'yxatga qo'shildi: {name}")
        return
    rows = store().products(active_only=True)
    await update.message.reply_text("Ruxsat ro'yxati:\n" + ("\n".join("• " + p["name"] for p in rows) or "bo'sh")
                                    + "\n\nQo'shish: /mahsulot nomi. O'chirish — HUD'dagi Blogerlar bo'limida.")


@admin_only
async def cmd_vet(update, context):
    if not context.args:
        await update.message.reply_text("Ishlatish: /baho @instagram")
        return
    c = store().creator_by_instagram(context.args[0])
    if not c:
        await update.message.reply_text("Bunday bloger yo'q.")
        return
    res = await run_vetting(context.bot, c["tg_id"])
    await update.message.reply_text(res["text"] if res else "Ochiq ma'lumotlarni olib bo'lmadi (IG_BD_TOKEN sozlanmagan yoki akkaunt yopiq).")


@admin_only
async def cmd_brief(update, context):
    text = " ".join(context.args).strip()
    store().set_setting("brief", text, update.effective_user.id)
    await update.message.reply_text("✅ Topshiriq saqlandi." if text else "Topshiriq o'chirildi.")


@admin_only
async def cmd_check(update, context):
    if not context.args:
        return
    v = store().voucher_by_code(context.args[0])
    if not v:
        await update.message.reply_text("Bunday kod yo'q.")
        return
    c = store().creator(v["tg_id"])
    await update.message.reply_text(f"{v['code']} — {v['status']}\nQiymati: {R.som(v['nominal'])}\n"
                                    f"Muddati: {R.tashkent_str(v['expires_at'])}\nBloger: @{c['instagram']}")


@admin_only
async def cmd_pending(update, context):
    rows = store().pending_creators()
    if not rows:
        await update.message.reply_text("Kutilayotgan ariza yo'q.")
    for c in rows:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Tasdiqlash", callback_data=f"cr|ca|{c['tg_id']}"),
                                    InlineKeyboardButton("❌ Rad etish", callback_data=f"cr|cx|{c['tg_id']}")]])
        await update.message.reply_text(f"{c['first_name']} (@{c['username']})\nTel: {c['phone']}\n"
                                        f"Instagram: https://instagram.com/{c['instagram']}", reply_markup=kb)


# ================= fon jarayoni =================

async def tick(b):
    st = store()
    linked = {l["tg_id"]: l for l in st.ig_links("ok")}
    for link in linked.values():
        await sync_creator(b, link)
    linked = {l["tg_id"]: l for l in st.ig_links("ok")}  # uzilganlar chiqib ketadi
    for r in st.close_due_reels():
        if r["tg_id"] in linked:
            await finalize_linked(b, st.reel(r["id"]))
            continue
        await safe_send(b, r["tg_id"], f"⏱ 30 kun yakunlandi: {r['url']}\nYakuniy Insights skrinshotini 24 soat ichida yuboring.")
        await notify_admins(b, f"⏱ 30 kun tugadi, yakuniy skrinshot kutilmoqda: {r['url']}")
    for r, day in st.due_reminders():
        if r["tg_id"] not in linked:  # ulangan akkauntlardan skrinshot so'ralmaydi
            await safe_send(b, r["tg_id"], f"📸 {day}-kun: {r['url']}\nInsights skrinshotini yuboring.",
                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
                                "📸 Skrinshot yuborish", callback_data=f"cr|shot|{r['id']}")]]))
        st.mark_reminded(r["id"], day)
    for tg_id, kind, code, amount, expires_at, days in st.expiry_reminders():
        what = f"{code} vaucheringiz ({R.som(amount)})" if kind == "voucher" else f"{R.som(amount)} qoldig'ingiz"
        await safe_send(b, tg_id, f"⏳ Eslatma: {what} muddati {days} kundan keyin tugaydi — "
                                  f"{R.tashkent_str(expires_at)} gacha. Do'konga kelib ishlatib qoling!")
    expired, carries = st.expire_old()
    for v in expired:
        await safe_send(b, v["tg_id"], f"⌛️ {v['code']} vaucher muddati tugadi.")
    for row in carries:
        await safe_send(b, row["tg_id"], f"⌛️ {R.som(row['amount'])} qoldiq muddati tugadi.")


async def loop(app):
    await asyncio.sleep(15)
    while True:
        try:
            await tick(app.bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Creator Rewards fon jarayonida xato")
        await asyncio.sleep(TICK_SEC)


def build_app(token):
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ishlat", cmd_redeem))
    app.add_handler(CommandHandler("promo", cmd_promo))
    app.add_handler(CommandHandler("daraja", cmd_level))
    app.add_handler(CommandHandler("brif", cmd_brief))
    app.add_handler(CommandHandler("tekshir", cmd_check))
    app.add_handler(CommandHandler("arizalar", cmd_pending))
    app.add_handler(CommandHandler("mahsulot", cmd_product))
    app.add_handler(CommandHandler("ulash", cmd_connect))
    app.add_handler(CommandHandler("baho", cmd_vet))
    app.add_handler(CallbackQueryHandler(on_callback, pattern=r"^cr\|"))
    app.add_handler(MessageHandler(filters.CONTACT & filters.ChatType.PRIVATE, on_contact))
    app.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, on_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, on_text))
    return app


async def start_bot(get_admin_ids):
    """Jarvis ishga tushganda chaqiriladi. CREATOR_BOT_TOKEN bo'lmasa, hech narsa qilmaydi."""
    token = (os.environ.get("CREATOR_BOT_TOKEN") or "").strip()
    if not token:
        log.info("CREATOR_BOT_TOKEN yo'q — blogerlar boti o'chiq.")
        return None
    _ctx["admins"] = get_admin_ids
    store().usd_rate = fx.cached_rate
    asyncio.get_running_loop().run_in_executor(None, fx.usd_rate)  # kursni oldindan yuklab qo'yamiz
    app = build_app(token)
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=False)
    _ctx["app"], _ctx["loop"] = app, asyncio.get_running_loop()
    url = hud_url()
    if url:
        try:
            await app.bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="Kabinet", web_app=WebAppInfo(url=url)))
        except Exception:
            log.exception("Kabinet tugmasini o'rnatib bo'lmadi")
    app.bot_data["loop"] = asyncio.create_task(loop(app))
    log.info("Blogerlar boti ishga tushdi.")
    return app


__all__ = ["LEVELS", "start_bot", "run_in_bot", "store"]
