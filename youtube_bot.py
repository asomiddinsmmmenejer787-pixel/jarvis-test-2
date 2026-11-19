"""
youtube_bot.py
--------------
Jarvis Telegram botining YouTube buyruqlari.

  /ytkey AIza...      - YouTube API kalitini saqlaydi (xabar darhol o'chiriladi).
                        Kalitni buyruqsiz tashlasangiz ham qabul qiladi.
  /yt <havola> [izoh] - video uchun sarlavha, tavsif, hashtag, teg taklifi.
                        Havolani buyruqsiz tashlasangiz ham ishlaydi.
  /ytulash            - kanalni ulash (ixtiyoriy). Ulansa, "Videoga yozish" tugmasi chiqadi.
  /ytholat            - kalit, kanal va bugungi kvota holati.

telegram_runner.py faqat setup() ni chaqiradi - mavjud Instagram qismiga tegilmaydi.
"""

import asyncio
import logging
import re

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

import youtube_api as yt
import youtube_seo as seo

logger = logging.getLogger(__name__)

YT_KEY_RE = re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")
YT_LINK_FILTER = filters.Regex(r"(?i)(youtube\.com/|youtu\.be/)")
YT_KEY_FILTER = filters.Regex(YT_KEY_RE.pattern)

router = APIRouter()

_deps = {}


# ---------- OAuth callback (brauzerda ochiladi) ----------

def _page(title, text, ok=True):
    color = "#d9a441" if ok else "#c0563f"
    return HTMLResponse(
        f"""<!doctype html><html lang="uz"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title></head>
<body style="font-family:system-ui;background:#14110d;color:#eee;display:flex;align-items:center;
justify-content:center;min-height:100vh;margin:0;text-align:center;padding:24px">
<div><h2 style="color:{color}">{title}</h2><p>{text}</p></div></body></html>""",
        status_code=200 if ok else 400,
    )


@router.get("/youtube/callback")
async def youtube_callback(request: Request):
    q = request.query_params
    if q.get("error"):
        return _page("Ruxsat berilmadi", "Telegram'ga qaytib, /ytulash ni qayta bosing.", ok=False)
    try:
        channel = await asyncio.to_thread(yt.finish_oauth, q.get("code", ""), q.get("state", ""))
    except yt.YouTubeError as e:
        return _page("Ulanmadi", str(e), ok=False)
    bot, owner = _deps.get("bot"), _deps.get("get_owner_id", lambda: None)()
    if bot and owner:
        try:
            await bot.send_message(
                chat_id=owner,
                text=f"✅ YouTube kanal ulandi{': ' + channel if channel else ''}\n"
                     "Endi takliflar ostida «Videoga yozish» tugmasi chiqadi.",
            )
        except Exception:
            logger.warning("Kanal ulangani haqida xabar yuborib bo'lmadi.")
    return _page("YouTube ulandi ✅", f"{channel or 'Kanal'} Jarvis'ga ulandi. Bu oynani yopib, Telegram'ga qayting.")


# ---------- yordamchilar ----------

def _err(e):
    if isinstance(e, yt.YouTubeError):
        if "kaliti yo'q" in str(e):
            return (
                "YouTube API kaliti hali yo'q.\n\n"
                "Google Cloud → Credentials'dan olgan kalitni shu yerga tashlang yoki:\n/ytkey AIza..."
            )
        return f"YouTube: {e}"
    return f"Xatolik: {type(e).__name__}"


def _keyboard(item):
    pid = item["id"]
    rows = []
    if yt.is_connected():
        n = min(3, 1 + len(item["result"].get("alt_titles", [])))
        rows.append([InlineKeyboardButton("✅ 1-sarlavha bilan yozish", callback_data=f"yt|ok|{pid}|0")])
        if n > 1:
            rows.append([
                InlineKeyboardButton(f"{i + 1}-sarlavha bilan", callback_data=f"yt|ok|{pid}|{i}")
                for i in range(1, n)
            ])
    rows.append([
        InlineKeyboardButton("🔁 Boshqa variant", callback_data=f"yt|again|{pid}"),
        InlineKeyboardButton("❌ Kerak emas", callback_data=f"yt|no|{pid}"),
    ])
    return InlineKeyboardMarkup(rows)


async def _run_optimize(bot, chat_id, video_id, hint):
    ai = _deps["ai"]()
    status = await bot.send_message(chat_id=chat_id, text="⏳ Video va nishadagi top videolar o'rganilmoqda...")
    try:
        item = await asyncio.to_thread(seo.optimize, ai, video_id, hint)
    except Exception as e:  # noqa: BLE001 - foydalanuvchiga tushunarli xabar
        if not isinstance(e, yt.YouTubeError):
            logger.exception("YouTube SEO xatosi")
        await status.edit_text(_err(e))
        return
    try:
        await status.delete()
    except Exception:
        pass
    await _deps["send_long"](bot, chat_id, seo.suggestion_text(item))
    footer = (
        "Qaysi sarlavhani videoga yozay?"
        if yt.is_connected()
        else "Matnni YouTube Studio'ga nusxalang.\nJarvis o'zi yozishi uchun kanalni ulang: /ytulash"
    )
    await bot.send_message(chat_id=chat_id, text=footer, reply_markup=_keyboard(item))


# ---------- buyruqlar ----------

async def ytkey_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    key = context.args[0] if context.args else ""
    if not key:
        await msg.reply_text(
            "YouTube API kalitini shunday yuboring:\n/ytkey AIza...\n\n"
            "Yoki kalitning o'zini tashlang. Xabaringiz darhol o'chiriladi."
        )
        return
    try:
        await msg.delete()
    except Exception:
        logger.warning("Kalit yozilgan xabarni o'chirib bo'lmadi.")
    chat_id = update.effective_chat.id
    try:
        await asyncio.to_thread(yt.set_api_key, key)
    except yt.YouTubeError as e:
        await context.bot.send_message(chat_id=chat_id, text=f"❌ {e}")
        return
    await context.bot.send_message(
        chat_id=chat_id,
        text="✅ YouTube API kaliti saqlandi.\n\n"
             "Endi video havolasini tashlang, Jarvis sarlavha, tavsif, hashtag va teglarni tayyorlaydi.\n"
             "Video nima haqida ekanini ham yozsangiz, natija aniqroq chiqadi:\n"
             "https://youtu.be/... rolikli krossovka, narxi 350 000",
    )


async def yt_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args or [])
    video_id = yt.parse_video_id(text)
    if not video_id:
        await update.message.reply_text(
            "Video havolasini yuboring:\n/yt https://youtu.be/... video haqida qisqa izoh\n\n"
            "Maslahat: videoni avval Unlisted yoki Private qilib yuklang, Jarvis'ning taklifini qo'ying, "
            "keyin Public qiling."
        )
        return
    hint = yt.LINK_RE.sub("", text).strip()
    hint = "" if hint == video_id else hint.replace(video_id, "").strip()
    if not yt.has_key() and not yt.is_connected():
        await update.message.reply_text(_err(yt.YouTubeError("YouTube API kaliti yo'q")))
        return
    await _run_optimize(context.bot, update.effective_chat.id, video_id, hint)


async def yt_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Buyruqsiz tashlangan kalit yoki video havolasi."""
    text = update.message.text or ""
    key = YT_KEY_RE.search(text)
    if key:
        context.args = [key.group(0)]
        await ytkey_cmd(update, context)
        return
    context.args = text.split()
    await yt_cmd(update, context)


async def ytulash_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args and context.args[0].lower() in ("uz", "uzish", "off"):
        await asyncio.to_thread(yt.disconnect)
        await update.message.reply_text("YouTube kanal uzildi. API kalit saqlanib qoldi.")
        return
    if not yt.oauth_available():
        await update.message.reply_text(
            "Kanalni ulash uchun Google Cloud'da bir marta sozlash kerak:\n\n"
            "1. APIs & Services → OAuth consent screen: External, ilova nomi Jarvis, "
            "Test users'ga o'z Gmail'ingizni qo'shing. Keyin «Publish app» → In production "
            "(aks holda ruxsat 7 kunda o'chadi).\n"
            "2. Credentials → Create credentials → OAuth client ID → Web application.\n"
            f"3. Authorized redirect URIs'ga shuni qo'shing:\n{yt.redirect_uri() or 'https://<railway-domen>/youtube/callback'}\n"
            "4. Client ID va Client secret'ni Railway → Variables'ga qo'shing:\n"
            "YT_CLIENT_ID=...\nYT_CLIENT_SECRET=...\n\n"
            "Railway qayta ishga tushgach, /ytulash ni yana bosing."
        )
        return
    try:
        link = await asyncio.to_thread(yt.auth_link)
    except yt.YouTubeError as e:
        await update.message.reply_text(f"❌ {e}")
        return
    await update.message.reply_text(
        "Pastdagi tugmani bosing, YouTube kanalingiz turgan Gmail'ni tanlang va ruxsat bering.\n\n"
        "«Google hasn't verified this app» chiqsa: Advanced → Go to Jarvis. Bu sizning o'z ilovangiz.\n"
        "Havola 15 daqiqa amal qiladi.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔗 YouTube'ni ulash", url=link)]]),
    )


async def ytholat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = yt.status()
    lines = [
        "📺 YouTube holati",
        f"API kalit: {'✅ bor' if s['key'] else '❌ yo`q (/ytkey)'}",
        f"Kanal: {'✅ ' + (s['channel'] or 'ulangan') if s['connected'] else '— ulanmagan (/ytulash, ixtiyoriy)'}",
        f"Bugungi kvota: {s['quota_used']} / {s['quota_limit']}",
        "(1 ta video tahlili ≈ 103 birlik, videoga yozish ≈ 51)",
    ]
    await update.message.reply_text("\n".join(lines))


async def yt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = (query.data or "").split("|")
    if len(parts) < 3:
        await query.answer()
        return
    action, pid = parts[1], parts[2]
    await query.answer("Bajarilmoqda...")
    chat_id = query.message.chat_id

    if action == "no":
        seo.pop_pending(pid)
        result = "Bekor qilindi."
    elif action == "again":
        item = seo.get_pending(pid)
        if not item:
            result = "Taklif eskirgan. Havolani qayta yuboring."
        else:
            seo.pop_pending(pid)
            await query.edit_message_text((query.message.text or "") + "\n\n— Yangi variant tayyorlanmoqda...")
            await _run_optimize(context.bot, chat_id, item["video_id"], item.get("hint", ""))
            return
    elif action == "ok":
        idx = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        try:
            result = await asyncio.to_thread(seo.apply, pid, idx)
        except yt.YouTubeError as e:
            result = f"❌ {e}"
    else:
        result = "Noma'lum amal."

    try:
        await query.edit_message_text((query.message.text or "") + "\n\n— " + result)
    except Exception:
        await context.bot.send_message(chat_id=chat_id, text=result)


# ---------- ulash ----------

def setup(app, owner_only, get_ai, send_long, get_owner_id):
    """telegram_runner.build_app() ichida, umumiy matn handleridan OLDIN chaqiriladi."""
    _deps.update(ai=get_ai, send_long=send_long, get_owner_id=get_owner_id, bot=app.bot)
    app.add_handler(CommandHandler("ytkey", owner_only(ytkey_cmd)))
    app.add_handler(CommandHandler("yt", owner_only(yt_cmd)))
    app.add_handler(CommandHandler("ytulash", owner_only(ytulash_cmd)))
    app.add_handler(CommandHandler("ytholat", owner_only(ytholat_cmd)))
    app.add_handler(CallbackQueryHandler(owner_only(yt_callback), pattern=r"^yt\|"))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & (YT_KEY_FILTER | YT_LINK_FILTER),
        owner_only(yt_text),
    ))
