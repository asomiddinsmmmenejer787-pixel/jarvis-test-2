"""
creator_rewards/bot.py
----------------------
Blogerlar uchun alohida Telegram bot (CREATOR_BOT_TOKEN). Jarvis'ning shaxsiy botiga tegmaydi.

Bloger:  ro'yxatdan o'tish (telefon + Instagram), Reels qo'shish, skrinshot yuborish,
         vaucher, HUD (mini ilova), administrator bilan yozishish.
Admin:   arizani tasdiqlash, skrinshotdagi raqamni tasdiqlash, mukofotni tasdiqlash,
         /ishlat KOD SUMMA — xariddan keyin vaucherni ishlatish, /tekshir KOD, /arizalar.
"""

import asyncio
import logging
import os

from telegram import (InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, MenuButtonWebApp,
                      ReplyKeyboardMarkup, ReplyKeyboardRemove, Update, WebAppInfo)
from telegram.ext import (ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes,
                          MessageHandler, filters)

from . import rewards as R
from .store import Store, StoreError

log = logging.getLogger("creator_bot")
TICK_SEC = int(os.environ.get("CREATOR_TICK_SEC", "600") or 600)

BTN_HUD = "📱 Kabinet"
BTN_REEL = "➕ Reels qo'shish"
BTN_SHOT = "📸 Skrinshot yuborish"
BTN_VOUCHER = "🎟 Vaucherim"
BTN_CHAT = "✉️ Administratorga yozish"

RULES_TEXT = (
    "Hamkorlik qoidalari:\n"
    "• Instagram akkauntingiz professional (Biznes yoki Avtor) va ochiq bo'lsin.\n"
    "• Video do'konda olinadi, 30–45 soniya, 9:16.\n"
    "• Reels Bolalar Dunyosi bilan Collab qilib joylanadi va doimiy turadi.\n"
    "• Videoni joylagan kuningiz havolasini botga yuboring: 30 kun shu paytdan hisoblanadi.\n"
    "• Mukofot: 10 000 ko'rish = 100 000 so'm, keyin har 1 000 ko'rishga +4 000 so'm, eng ko'pi 460 000 so'm.\n"
    "• Vaucher faqat do'konda, naqd pulga almashtirilmaydi, 30 kun amal qiladi.\n"
    "• Sun'iy ko'rish yoki soxta skrinshot aniqlansa, hisob to'xtatiladi."
)

_ctx = {"store": None, "admins": lambda: set()}


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


def main_keyboard():
    url = hud_url()
    first = [KeyboardButton(BTN_HUD, web_app=WebAppInfo(url=url))] if url else []
    rows = [first + [KeyboardButton(BTN_VOUCHER)], [KeyboardButton(BTN_REEL), KeyboardButton(BTN_SHOT)],
            [KeyboardButton(BTN_CHAT)]]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


async def notify_admins(bot, text, **kw):
    sent = []
    for a in admins():
        try:
            sent.append(await bot.send_message(a, text, **kw))
        except Exception:
            log.exception("Administratorga xabar yuborilmadi")
    return sent


# ---------------- bloger: ro'yxatdan o'tish ----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    c = store().upsert_telegram(u.id, u.first_name, u.last_name, u.username)
    context.user_data.clear()
    if is_admin(u.id):
        await update.message.reply_text(
            "Administrator rejimi.\n/arizalar — kutilayotgan arizalar\n/ishlat KOD SUMMA [chek] — vaucherni ishlatish\n"
            "/tekshir KOD — vaucher holati\nBlogerga javob berish uchun uning xabariga Reply qiling.")
        return
    if c["status"] == "approved":
        await update.message.reply_text(f"Xush kelibsiz, {u.first_name}! Kabinetingiz tayyor.", reply_markup=main_keyboard())
    elif c["status"] == "pending":
        await update.message.reply_text("Arizangiz ko'rib chiqilmoqda. Tasdiqlangach shu yerga xabar keladi.")
    elif c["status"] == "blocked":
        await update.message.reply_text("Hisobingiz to'xtatilgan. Savollar bo'lsa, shu yerga yozing.")
    else:
        kb = ReplyKeyboardMarkup([[KeyboardButton("📞 Telefon raqamni yuborish", request_contact=True)]],
                                 resize_keyboard=True, one_time_keyboard=True)
        await update.message.reply_text(
            f"Assalomu alaykum, {u.first_name}! Bolalar Dunyosi Creator Rewards dasturiga xush kelibsiz.\n\n"
            "Ro'yxatdan o'tish uchun telefon raqamingizni yuboring.", reply_markup=kb)


async def on_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u, contact = update.effective_user, update.message.contact
    if contact.user_id != u.id:
        await update.message.reply_text("Iltimos, o'zingizning raqamingizni tugma orqali yuboring.")
        return
    store().set_phone(u.id, contact.phone_number)
    context.user_data["await"] = "instagram"
    await update.message.reply_text("Rahmat! Endi Instagram username'ingizni yozing (masalan: @madina_blog).",
                                    reply_markup=ReplyKeyboardRemove())


async def ask_rules(update, ig):
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Roziman, ariza yuborish", callback_data="cr|apply")]])
    await update.message.reply_text(f"Instagram: @{ig}\n\n{RULES_TEXT}", reply_markup=kb)


# ---------------- bloger: menyu ----------------

async def voucher_text(tg_id):
    ov = store().overview(tg_id)
    b = ov["balance"]
    lines = [f"Asosiy balans: {R.som(b['available'])}", f"Kutilayotgan (30 kun ichida): {R.som(b['pending'])}"]
    v = ov["voucher"]
    if v:
        lines.append(f"\nFaol vaucher: {v['code']}\nQiymati: {R.som(v['nominal'])}\nAmal qiladi: {R.tashkent_str(v['expires_at'])} gacha")
    if ov["carry"]:
        lines.append(f"\nQoldiq: {R.som(ov['carry']['amount'])} — keyingi vaucheringizga qo'shiladi "
                     f"({R.tashkent_str(ov['carry']['expires_at'], False)} gacha).")
    if ov["profile"]["promo_code"]:
        lines.append(f"\nObunachilaringiz uchun promo-kod: {ov['profile']['promo_code']}")
    return "\n".join(lines)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u, text = update.effective_user, (update.message.text or "").strip()
    st = store()
    c = st.upsert_telegram(u.id, u.first_name, u.last_name, u.username)
    if is_admin(u.id):
        await admin_text(update, context, text)
        return
    state = context.user_data.get("await")
    try:
        if state == "instagram":
            ig = st.set_instagram(u.id, text)
            context.user_data.pop("await", None)
            await ask_rules(update, ig)
            return
        if state == "reel":
            rid = st.add_reel(u.id, text)
            context.user_data.pop("await", None)
            r = st.reel(rid)
            await update.message.reply_text(
                f"✅ Video ro'yxatga olindi.\nKuzatuv {R.tashkent_str(r['ends_at'])} gacha davom etadi.\n"
                "1, 7, 14 va 30-kunlarda Insights skrinshotini so'rayman.", reply_markup=main_keyboard())
            await notify_admins(context.bot, f"🎬 Yangi Reels: @{c['instagram']}\n{r['url']}")
            return
        if state and state.startswith("views:"):
            rid, file_id = state.split(":", 2)[1:]
            views = int(text.replace(" ", "").replace(",", "").replace(".", ""))
            sid = st.add_snapshot(int(rid), views, "screenshot", file_id=file_id, actor=u.id)
            context.user_data.pop("await", None)
            await update.message.reply_text("Rahmat! Skrinshot administratorga yuborildi.", reply_markup=main_keyboard())
            r = st.reel(int(rid))
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ To'g'ri", callback_data=f"cr|sv|{sid}"),
                                        InlineKeyboardButton("✏️ Boshqa raqam", callback_data=f"cr|se|{sid}")]])
            for a in admins():
                try:
                    await context.bot.send_photo(
                        a, file_id, reply_markup=kb,
                        caption=f"📸 @{c['instagram']} — {r['url']}\nBloger yozgan ko'rishlar: {views:,}".replace(",", " "))
                except Exception:
                    log.exception("Skrinshot administratorga yuborilmadi")
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
    if text == BTN_REEL:
        context.user_data["await"] = "reel"
        await update.message.reply_text("Instagram'da joylagan Collab Reels havolasini yuboring.")
    elif text == BTN_SHOT:
        reels = [r for r in st.reels_of(u.id) if r["status"] in ("monitoring", "awaiting_final")]
        if not reels:
            await update.message.reply_text("Hozir kuzatuvdagi video yo'q.")
            return
        kb = [[InlineKeyboardButton(f"{r['shortcode']} · {R.tashkent_str(r['posted_at'], False)}",
                                    callback_data=f"cr|shot|{r['id']}")] for r in reels[:10]]
        await update.message.reply_text("Qaysi video uchun?", reply_markup=InlineKeyboardMarkup(kb))
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
    state = context.user_data.get("await", "")
    if not state.startswith("photo:"):
        await update.message.reply_text(f"Skrinshot yuborish uchun avval «{BTN_SHOT}» tugmasini bosing.")
        return
    rid = state.split(":")[1]
    file_id = update.message.photo[-1].file_id
    context.user_data["await"] = f"views:{rid}:{file_id}"
    await update.message.reply_text("Skrinshotdagi ko'rishlar (Views) sonini raqam bilan yozing:")
    log.info("Skrinshot qabul qilindi | user=%s reel=%s", u.id, rid)


# ---------------- tugmalar ----------------

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split("|")
    action, arg = parts[1], (parts[2] if len(parts) > 2 else "")
    u = q.from_user
    st = store()
    try:
        if action == "apply":
            c = st.submit_application(u.id)
            await q.answer("Ariza yuborildi")
            await q.edit_message_text("✅ Arizangiz yuborildi. Administrator tasdiqlagach xabar beraman.")
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Tasdiqlash", callback_data=f"cr|ca|{u.id}"),
                                        InlineKeyboardButton("❌ Rad etish", callback_data=f"cr|cx|{u.id}")]])
            await notify_admins(context.bot, f"🆕 Yangi ariza\n{c['first_name']} {c['last_name']} (@{c['username']})\n"
                                             f"Tel: {c['phone']}\nInstagram: https://instagram.com/{c['instagram']}",
                                reply_markup=kb)
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
            c = st.decide_creator(int(arg), action == "ca", u.id)
            await q.answer("Saqlandi")
            await q.edit_message_text(q.message.text + ("\n\n✅ Tasdiqlandi" if action == "ca" else "\n\n❌ Rad etildi"))
            if action == "ca":
                await context.bot.send_message(int(arg), "🎉 Arizangiz tasdiqlandi! Endi do'konga kelib video olishingiz mumkin.\n"
                                                         f"Obunachilaringiz uchun promo-kod: {c['promo_code']}",
                                               reply_markup=main_keyboard())
            else:
                await context.bot.send_message(int(arg), "Afsuski, arizangiz hozircha tasdiqlanmadi. Savollar bo'lsa, shu yerga yozing.")
        elif action == "sv":
            await q.answer("Tasdiqlandi")
            await after_verify(context, st, int(arg), u.id, None, q)
        elif action == "se":
            context.user_data["await"] = f"fix:{int(arg)}"
            await q.answer()
            await q.message.reply_text("Skrinshotdagi to'g'ri ko'rishlar sonini yozing:")
        elif action in ("ra", "rx"):
            await q.answer("Saqlandi")
            rw = st.reward(int(arg))
            reel = st.reel(rw["reel_id"])
            v = st.decide_reward(int(arg), action == "ra", u.id)
            if v:
                await q.edit_message_text(q.message.text + f"\n\n✅ Vaucher chiqarildi: {v['code']}")
                extra = f"\n(Oldingi qoldiq {R.som(v['carried_in'])} ham qo'shildi.)" if v["carried_in"] else ""
                await context.bot.send_message(reel["tg_id"],
                    f"🎟 Yangi vaucher!\nKod: {v['code']}\nQiymati: {R.som(v['nominal'])}{extra}\n"
                    f"Amal qiladi: {R.tashkent_str(v['expires_at'])} gacha.\nKod bir marta ishlatiladi, qoldiq keyingi vaucherga o'tadi.")
            else:
                await q.edit_message_text(q.message.text + "\n\n❌ Rad etildi")
                await context.bot.send_message(reel["tg_id"], f"Video bo'yicha mukofot tasdiqlanmadi: {reel['url']}")
    except StoreError as e:
        await q.answer(str(e)[:190], show_alert=True)


async def after_verify(context, st, snap_id, admin_id, views, q=None):
    reward = st.verify_snapshot(snap_id, admin_id, views)
    snap = st.snapshot(snap_id)
    reel = st.reel(snap["reel_id"])
    if q is not None:
        await q.edit_message_caption((q.message.caption or "") + f"\n\n✅ Tasdiqlandi: {snap['views']:,}".replace(",", " "))
    est = R.reward_for_views(reel["last_views"])
    await context.bot.send_message(reel["tg_id"], f"✅ Skrinshot tasdiqlandi: {reel['last_views']:,} ko'rish.\n"
                                                  f"Hozirgi taxminiy mukofot: {R.som(est)}".replace(",", " "))
    if reward:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Vaucher chiqarish", callback_data=f"cr|ra|{reward['id']}"),
                                    InlineKeyboardButton("❌ Rad etish", callback_data=f"cr|rx|{reward['id']}")]])
        c = st.creator(reel["tg_id"])
        await notify_admins(context.bot, f"💰 Yakuniy hisob-kitob\n@{c['instagram']} — {reel['url']}\n"
                                         f"30 kunlik ko'rishlar: {reward['views']:,}\nMukofot: {R.som(reward['amount'])}".replace(",", " "),
                            reply_markup=kb)
    elif reel["status"] == "no_reward":
        await context.bot.send_message(reel["tg_id"], "30 kun yakunlandi. Afsuski, 10 000 ko'rishga yetmadi, bu video uchun mukofot yo'q.")


# ---------------- administrator ----------------

async def admin_text(update, context, text):
    st = store()
    state = context.user_data.get("await", "")
    replied = update.message.reply_to_message
    try:
        if state.startswith("fix:"):
            views = int(text.replace(" ", "").replace(",", ""))
            context.user_data.pop("await", None)
            await after_verify(context, st, int(state.split(":")[1]), update.effective_user.id, views)
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
    await update.message.reply_text("Buyruqlar: /arizalar, /ishlat KOD SUMMA [chek], /tekshir KOD")


async def cmd_redeem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Ishlatish: /ishlat BD-XXXX-XXXX 180000 [chek raqami]")
        return
    try:
        res = store().redeem(args[0], int(args[1].replace(" ", "")), update.effective_user.id, " ".join(args[2:]))
    except (StoreError, ValueError) as e:
        await update.message.reply_text(f"❌ {e}")
        return
    left = (f"Qoldiq: {R.som(res['left'])} — keyingi vaucheringizga qo'shiladi "
            f"({R.tashkent_str(res['left_expires_at'], False)} gacha amal qiladi)." if res["left"] else "Qoldiq yo'q.")
    await context.bot.send_message(res["tg_id"], f"🛍 Vaucheringiz ishlatildi.\nVaucher: {R.som(res['nominal'])}\n"
                                                 f"Ishlatildi: {R.som(res['used'])}\n{left}")
    await update.message.reply_text(f"✅ {res['code']}: {R.som(res['used'])} ishlatildi, qoldiq {R.som(res['left'])}.")


async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id) or not context.args:
        return
    v = store().voucher_by_code(context.args[0])
    if not v:
        await update.message.reply_text("Bunday kod yo'q.")
        return
    c = store().creator(v["tg_id"])
    await update.message.reply_text(f"{v['code']} — {v['status']}\nQiymati: {R.som(v['nominal'])}\n"
                                    f"Muddati: {R.tashkent_str(v['expires_at'])}\nBloger: @{c['instagram']}")


async def cmd_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    rows = store().pending_creators()
    if not rows:
        await update.message.reply_text("Kutilayotgan ariza yo'q.")
    for c in rows:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Tasdiqlash", callback_data=f"cr|ca|{c['tg_id']}"),
                                    InlineKeyboardButton("❌ Rad etish", callback_data=f"cr|cx|{c['tg_id']}")]])
        await update.message.reply_text(f"{c['first_name']} (@{c['username']})\nTel: {c['phone']}\n"
                                        f"Instagram: https://instagram.com/{c['instagram']}", reply_markup=kb)


# ---------------- fon jarayoni ----------------

async def tick(bot):
    st = store()
    for r in st.close_due_reels():
        try:
            await bot.send_message(r["tg_id"], f"⏱ 30 kun yakunlandi: {r['url']}\nYakuniy Insights skrinshotini 24 soat ichida yuboring.")
        except Exception:
            log.exception("Blogerga xabar yuborilmadi")
        await notify_admins(bot, f"⏱ 30 kun tugadi, yakuniy skrinshot kutilmoqda: {r['url']}")
    for r, day in st.due_reminders():
        try:
            await bot.send_message(r["tg_id"], f"📸 {day}-kun: {r['url']}\nInsights skrinshotini yuboring — «{BTN_SHOT}».")
        except Exception:
            log.exception("Eslatma yuborilmadi")
        st.mark_reminded(r["id"], day)
    expired, carries = st.expire_old()
    for v in expired:
        try:
            await bot.send_message(v["tg_id"], f"⌛️ {v['code']} vaucher muddati tugadi.")
        except Exception:
            pass
    for row in carries:
        try:
            await bot.send_message(row["tg_id"], f"⌛️ {R.som(row['amount'])} qoldiq muddati tugadi.")
        except Exception:
            pass


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
    app.add_handler(CommandHandler("tekshir", cmd_check))
    app.add_handler(CommandHandler("arizalar", cmd_pending))
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
    app = build_app(token)
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=False)
    url = hud_url()
    if url:
        try:
            await app.bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="Kabinet", web_app=WebAppInfo(url=url)))
        except Exception:
            log.exception("Kabinet tugmasini o'rnatib bo'lmadi")
    app.bot_data["loop"] = asyncio.create_task(loop(app))
    log.info("Blogerlar boti ishga tushdi.")
    return app
