"""Jarvis Telegram runner.
Telegram bot, Instagram tahlili, komentlarga javob tizimi va
Instagram OAuth callback web-serverini ishga tushiradi."""

import asyncio
import functools
import json
import logging
import os
import re
import threading
import time
from datetime import datetime

import config
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse

import ads_api
import agency_bridge
import analytics
import audit
import brain
import followers_live
import reel_sources
import catalog
import instagram_api as ig
import webapp
import youtube_bot
from creator_rewards import bot as creator_bot
from creator_rewards import web as creator_web
from auto_reply import CATEGORY_NAMES, CommentResponder
from dm_chat import DirectChat
from instagram_api import InstagramError
from core.jarvis import Jarvis
from ai.base import AIError, is_error_text
from ai.fallback import FallbackProvider
from ai.gemini import GeminiProvider
from ai.groq import GroqError, GroqProvider, check_key as check_groq_key, save_api_key as save_groq_key
from ai.mock import MockProvider

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonWebApp,
    Update,
    WebAppInfo,
)
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)


# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
# httpx har bir so'rov URL'ini (bot tokeni bilan) logga yozmasin
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)
audit.install()

TELEGRAM_LIMIT = 4000
CHECK_EVERY_SEC = max(30, int(os.environ.get("CHECK_EVERY_SEC", "60") or 60))
GROQ_KEY_RE = re.compile(r"gsk_[A-Za-z0-9]{30,}")
IG_TOKEN_RE = re.compile(r"IG[A-Za-z0-9_\-]{40,}")
JARVIS_COMMANDS = ["script", "hook", "caption", "telegram", "ad", "ideas", "ai_video"]


# Instagram OAuth callback web-server
web = FastAPI(
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@web.get("/")
async def root(request: Request):
    code = request.query_params.get("code")
    error = request.query_params.get("error")

    if code:
        # Maxfiy OAuth kodini logga chiqarmaymiz.
        logger.info("Instagram OAuth callback qabul qilindi.")
        return PlainTextResponse(
            "Callback qabul qilindi. Bu oynani yopishingiz mumkin."
        )

    if error:
        logger.warning("Instagram OAuth ruxsati olinmadi.")
        return PlainTextResponse(
            "Instagram ruxsatida xatolik yuz berdi.",
            status_code=400,
        )

    return PlainTextResponse("Jarvis ishlayapti")


def start_web_server():
    def run():
        try:
            port = int(os.environ.get("PORT", "8000"))
            logger.info("OAuth web-server ishga tushmoqda.")
            uvicorn.run(
                web,
                host="0.0.0.0",
                port=port,
                log_level="info",
                access_log=False,
            )
        except Exception:
            logger.exception("OAuth web-server ishga tushmadi.")

    thread = threading.Thread(
        target=run,
        daemon=True,
        name="jarvis-web-server",
    )
    thread.start()


# AI provider
def build_ai_provider():
    provider_name = getattr(config, "AI_PROVIDER", "mock")

    if provider_name == "groq":
        return FallbackProvider([GroqProvider()])

    if provider_name == "gemini":
        api_key = getattr(config, "GEMINI_API_KEY", "")
        model = getattr(config, "GEMINI_MODEL", "")

        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY topilmadi. Railway Variables'ni tekshiring."
            )

        if not model:
            raise RuntimeError(
                "GEMINI_MODEL topilmadi. Config sozlamalarini tekshiring."
            )

        # Gemini asosiy, Groq zaxira: Gemini limiti tugasa avtomatik Groq ishlaydi.
        # Groq kaliti hali bo'lmasa ham qo'shiladi: /groqkey bilan keyin ulanadi.
        return FallbackProvider([GeminiProvider(api_key=api_key, model=model), GroqProvider()])

    logger.info("MockProvider tanlandi.")
    return MockProvider()


# Jarvis instance
jarvis = Jarvis(
    ai_provider=build_ai_provider(),
    memory_dir=config.MEMORY_DIR,
)

# Komentlarga javob tizimi (faqat haqiqiy Gemini bilan ishlaydi)
responder = (
    CommentResponder(jarvis.ai_provider)
    if hasattr(jarvis.ai_provider, "generate_json")
    else None
)


# Direktga o'zi yozgan mijozlarga javob
chat = DirectChat(jarvis.ai_provider) if responder is not None else None


# ---------- mini ilova (Jarvis HUD) ----------

web.include_router(webapp.router)
web.include_router(youtube_bot.router)
web.include_router(creator_web.router)


# ---------- bot egasi ----------

def _owner_file():
    return os.path.join(config.DATA_DIR, "owner.json")


def get_owner_id():
    if config.OWNER_CHAT_ID:
        return config.OWNER_CHAT_ID
    try:
        with open(_owner_file(), encoding="utf-8") as f:
            return int(json.load(f)["user_id"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def claim_owner(user_id):
    try:
        os.makedirs(config.DATA_DIR, exist_ok=True)
        with open(_owner_file(), "w", encoding="utf-8") as f:
            json.dump({"user_id": int(user_id)}, f)
    except OSError:
        logger.exception("Egani saqlab bo'lmadi.")


def owner_only(handler):
    """Faqat bot egasi foydalana oladi. Egasi hali yo'q bo'lsa, birinchi yozgan odam egasi bo'ladi."""

    @functools.wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if user is None:
            return
        owner = get_owner_id()
        if owner is None:
            claim_owner(user.id)
            logger.info("Bot egasi belgilandi | user_id=%s", user.id)
            if update.effective_message:
                await update.effective_message.reply_text(
                    "Siz Jarvis egasi sifatida belgilandingiz. Endi botdan faqat siz foydalana olasiz.\n\n"
                    f"Sizning ID: {user.id}\n"
                    f"Doimiy qilish uchun Railway Variables'ga OWNER_CHAT_ID={user.id} qo'shing."
                )
        elif user.id != owner:
            logger.warning("Begona foydalanuvchi rad etildi | user_id=%s", user.id)
            if update.callback_query:
                await update.callback_query.answer("Ruxsat yo'q")
            elif update.effective_message:
                await update.effective_message.reply_text("Bu shaxsiy bot.")
            return
        return await handler(update, context)

    return wrapper


@owner_only
async def app_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = webapp.webapp_url()
    if not url:
        await update.message.reply_text(
            "Mini ilova manzili topilmadi. Railway'da servisga public domen bo'lishi kerak."
        )
        return
    await update.message.reply_text(
        "Jarvis boshqaruv paneli:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🛰 Jarvis HUD'ni ochish", web_app=WebAppInfo(url=url))]]),
    )


# ---------- yordamchilar ----------

def split_text(text, limit=TELEGRAM_LIMIT):
    """Uzun matnni qatorlar bo'yicha bo'laklarga ajratadi."""
    parts, current = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            parts.append(current)
            current = line
        else:
            current = candidate
    if current.strip():
        parts.append(current)
    return parts or [""]


async def send_long(bot, chat_id, text, **kwargs):
    for part in split_text(text):
        if part.strip():
            await bot.send_message(chat_id=chat_id, text=part, disable_web_page_preview=True, **kwargs)


def ig_error_text(e):
    if getattr(e, "fatal", False):
        if getattr(e, "code", None) == "no_token":
            return instagram_setup_text()
        return (
            "Instagram token yaroqsiz yoki muddati o'tgan.\n"
            "Meta'dan yangi token olib, shu yerga yuboring: /igtoken IGAA..."
        )
    if getattr(e, "rate_limited", False):
        return "Instagram so'rovlar limiti to'ldi. 30-60 daqiqadan keyin qayta urinib ko'ring."
    return f"Instagram xatosi: {e}"


def instagram_setup_text():
    return (
        "Instagram hali ulanmagan.\n\n"
        "Meta'dan olingan tokenni shu yerga yuboring:\n/igtoken IGAA...\n\n"
        "Yoki Railway → Variables'ga INSTAGRAM_ACCESS_TOKEN qilib qo'shing."
    )


async def ensure_instagram(update):
    if ig.is_configured():
        return True
    await update.effective_message.reply_text(instagram_setup_text())
    return False


# ---------- oddiy buyruqlar ----------

HELP_TEXT = (
    "Assalomu alaykum! Men Jarvis — sizning SMM yordamchingizman.\n\n"
    "Kontent yaratish:\n"
    "/script <mavzu> — video ssenariysi\n"
    "/hook <mavzu> — hook variantlari\n"
    "/caption <mavzu> — Instagram caption\n"
    "/telegram <mavzu> — Telegram post\n"
    "/ad <mavzu> — reklama ssenariysi\n"
    "/ideas <mavzu> — kontent g'oyalari\n"
    "/ai_video <mavzu> — AI video uchun prompt\n\n"
    "/app — Jarvis HUD mini ilovasi\n\n"
    "Instagram:\n"
    "/insta — oxirgi 12 kontentning to'liq tahlili (/insta 20 — 20 tasi)\n"
    "/post 1 — bitta postni chuqur tahlil (1 = eng oxirgisi, yoki post havolasi)\n"
    "/stories — faol storylar statistikasi\n"
    "/autoreply — komentlarga javob tizimi holati va rejimi\n"
    "/pending — ruxsat kutayotgan komentlar\n"
    "/checknow — komentlarni hoziroq tekshirish\n"
    "/direkt — komentdan keyingi direkt xabar rejimi (tasdiq / avto / off)\n"
    "/yozishma — direktga yozganlarga javob rejimi (tasdiq / avto / off)\n"
    "/eslab <ma'lumot> — mahsulot, narx yoki boshqa ma'lumotni xotiraga saqlash\n"
    "/xotira — xotiradagi yozuvlar, /unut <raqam> — o'chirish\n"
    "/igtoken <token> — yangi Instagram tokenni ulash\n"
    "/diag — AI va Instagram holatini tekshirish\n"
    "/groqkey <kalit> — zaxira AI (Groq) kalitini ulash\n"
    "/limit <son> — kunlik avtomatik komment javoblari limiti\n\n"
    "YouTube:\n"
    "/yt <havola> [izoh] — sarlavha, tavsif, hashtag va teglar (havolani o'zini tashlasangiz ham bo'ladi)\n"
    "/ytkey <kalit> — YouTube API kalitini ulash\n"
    "/ytulash — kanalni ulash (Jarvis taklifni videoga o'zi yozadi)\n"
    "/ytholat — YouTube holati va bugungi kvota\n\n"
    "Yoki oddiy tilda savolingizni yozing."
)


@owner_only
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None:
        return
    logger.info(
        "START command qabul qilindi | chat_id=%s",
        update.effective_chat.id if update.effective_chat else "unknown",
    )
    await update.message.reply_text(HELP_TEXT)


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user and update.message:
        await update.message.reply_text(f"Sizning Telegram ID: {update.effective_user.id}")


async def answer_with_jarvis(update: Update, context: ContextTypes.DEFAULT_TYPE, user_text: str):
    chat_id = update.effective_chat.id

    logger.info(
        "HANDLE_MESSAGE ISHLADI | chat_id=%s | text_length=%s",
        chat_id,
        len(user_text),
    )

    try:
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        # Sinxron Jarvis/AI ishlashi Telegram event loop'ini
        # bloklamasligi uchun alohida threadda bajariladi.
        reply = await asyncio.to_thread(jarvis.handle, user_text)

        if not isinstance(reply, str):
            reply = str(reply)

        if not reply.strip():
            reply = (
                "Jarvis bo'sh javob qaytardi. "
                "Iltimos, boshqa savol bilan urinib ko'ring."
            )

        await send_long(context.bot, chat_id, reply)
        logger.info("Telegram javobi yuborildi | chat_id=%s | length=%s", chat_id, len(reply))

    except Exception:
        logger.exception("HANDLE_MESSAGE ichida xatolik | chat_id=%s", chat_id)
        try:
            await update.message.reply_text(
                "Kechirasiz, Jarvisda xatolik yuz berdi. "
                "Birozdan so'ng qayta urinib ko'ring."
            )
        except Exception:
            logger.exception("Xatolik haqida Telegram xabarini yuborib bo'lmadi.")


@owner_only
async def jarvis_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/script, /hook va boshqa kontent buyruqlarini Jarvis'ga uzatadi."""
    if update.message is None or not update.message.text:
        return
    # Guruhda "/script@BotNomi mavzu" ko'rinishida kelsa, bot nomini olib tashlaymiz
    text = re.sub(r"^(/\w+)@\w+", r"\1", update.message.text.strip())
    await answer_with_jarvis(update, context, text)


@owner_only
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None or update.effective_chat is None:
        logger.warning("Xabarda message yoki chat ma'lumoti yo'q.")
        return

    user_text = update.message.text
    if not user_text or not user_text.strip():
        return

    # Xabarda Groq kaliti bo'lsa - saqlaymiz, AI'ga yubormaymiz
    groq_match = GROQ_KEY_RE.search(user_text)
    if groq_match:
        context.args = [groq_match.group(0)]
        await groqkey_cmd.__wrapped__(update, context)
        return

    # Xabarda Instagram tokeni bo'lsa (qanday formatda bo'lmasin) - ulaymiz, AI'ga yubormaymiz
    token_match = IG_TOKEN_RE.search(user_text)
    if token_match:
        context.args = [token_match.group(0)]
        await igtoken_cmd.__wrapped__(update, context)
        return

    # "Eslab qol: ..." deb yozilsa, xotiraga saqlaymiz
    m = re.match(r"\s*(eslab qol|eslab qo['’`]?y|yodda tut|запомни)\s*[:,\-–—]?\s*(.+)", user_text,
                 re.IGNORECASE | re.DOTALL)
    if m:
        await remember(update, m.group(2))
        return

    # Ruxsat so'ralgan komentga egasi Reply qilib o'z javobini yozdi
    replied = update.message.reply_to_message
    if responder and replied is not None:
        chat_key = chat.find_by_message(replied.message_id) if chat else None
        if chat_key:
            try:
                result = await asyncio.to_thread(chat.approve, chat_key, user_text)
            except InstagramError as e:
                result = ig_error_text(e)
            await update.message.reply_text(result)
            return
        dm_cid = responder.find_dm_by_message(replied.message_id)
        if dm_cid:
            try:
                result = await asyncio.to_thread(responder.approve_dm, dm_cid, user_text)
            except InstagramError as e:
                result = ig_error_text(e)
            await update.message.reply_text(result)
            return
        cid = responder.find_by_message(replied.message_id)
        if cid:
            try:
                result = await asyncio.to_thread(responder.approve, cid, user_text)
            except InstagramError as e:
                result = ig_error_text(e)
            await update.message.reply_text(result)
            return

    ctx_text = user_text
    if replied is not None and (replied.text or replied.caption):
        # Egasi Jarvisning xabariga (masalan komentga javob haqidagi xabarga) Reply qilib ko'rsatma yozdi
        ctx_text += ("\n\n(Egasi Jarvisning shu xabariga Reply qilib yozdi: «"
                     + (replied.text or replied.caption)[:700] + "»)")
    await smart_answer(update, context, user_text, ctx_text)


# ---------- o'rganish: egasining tanbehi va ko'rsatmalari ----------

def _think(user_text):
    status, last_post, hist = brain.build_context(responder, chat, jarvis.memory)
    return brain.think(jarvis.ai_provider, user_text, status, last_post, hist)


async def run_recheck(bot, chat_id, n_posts):
    try:
        events, stats = await asyncio.to_thread(responder.recheck, n_posts)
    except InstagramError as e:
        await bot.send_message(chat_id=chat_id, text="⚠️ Qayta tekshirib bo'lmadi: " + ig_error_text(e))
        return
    except AIError as e:
        await bot.send_message(chat_id=chat_id, text=f"⚠️ Qayta tekshirishda AI xatosi: {e}")
        return
    except Exception:
        logger.exception("Qayta tekshiruvda xato")
        await bot.send_message(chat_id=chat_id, text="⚠️ Qayta tekshiruvda kutilmagan xato bo'ldi.")
        return
    if stats.get("error"):
        await bot.send_message(chat_id=chat_id, text="⚠️ " + stats["error"])
        return
    for ev in events:
        brain.log_event(ev)
        try:
            await notify_event(bot, chat_id, ev)
        except Exception:
            logger.exception("Voqeani yuborib bo'lmadi.")
    await send_unsent_approvals(bot, chat_id)
    text = (f"✅ Qayta tekshiruv tugadi: {stats.get('comments', 0)} ta komment ko'rildi.\n"
            f"Javobsiz qolgani: {stats.get('new', 0)}, javob yozildi: {stats.get('sent', 0)}, "
            f"sizga ruxsatga: {stats.get('approval', 0)}, spam: {stats.get('skipped', 0)}, "
            f"xato: {stats.get('ai_errors', 0) + stats.get('send_errors', 0)}")
    if stats.get("new", 0) > stats.get("sent", 0) + stats.get("approval", 0) + stats.get("skipped", 0):
        text += "\nQolganlarini keyingi tekshiruvlarda yozib boraman (bir martada 40 tagacha)."
    brain.log_entry(f"Qayta tekshiruv: javob {stats.get('sent', 0)}, ruxsatga {stats.get('approval', 0)}")
    await bot.send_message(chat_id=chat_id, text=text)


async def smart_answer(update: Update, context: ContextTypes.DEFAULT_TYPE, user_text: str, ctx_text=None):
    """Egasining xabari ish haqida bo'lsa (tanbeh, qoida, qayta tekshir) - o'zi tuzatadi."""
    if responder is None or not ig.is_configured():
        await answer_with_jarvis(update, context, user_text)
        return
    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        res = await asyncio.to_thread(_think, ctx_text or user_text)
    except Exception as e:  # noqa: BLE001
        logger.warning("Xabarni tahlil qilib bo'lmadi: %s", e)
        res = {"kind": "chat"}
    if res.get("kind") != "work":
        await answer_with_jarvis(update, context, user_text)
        return
    lines, recheck = brain.apply(res, responder, chat)
    reply = "\n".join(lines)
    await send_long(context.bot, update.effective_chat.id, reply)
    jarvis.memory.add("user", user_text)
    jarvis.memory.add("jarvis", reply)
    brain.log_entry(f"Egasi yozdi: «{user_text[:100]}»" + (f" → qoida: {res['lesson'][:120]}" if res["lesson"] else ""))
    if recheck:
        asyncio.create_task(run_recheck(context.bot, update.effective_chat.id, recheck))


@owner_only
async def qoidalar_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = brain.get_lessons()
    if not items:
        await update.message.reply_text(
            "Hali qoida yo'q. Menga oddiy so'z bilan tanbeh yozing, masalan: "
            "\"belgilangan komentlarga ham javob yoz, qisqa bo'lsin\" — o'zim qoida qilib saqlayman.")
        return
    await send_long(context.bot, update.effective_chat.id,
                    "📌 Jarvis qoidalari (har javobda amal qiladi):\n"
                    + "\n".join(f"{i}. {x}" for i, x in enumerate(items, 1))
                    + "\n\nO'chirish: /qoidaochir <raqam>")


@owner_only
async def qoidaochir_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Qaysi qoidani? Masalan: /qoidaochir 2 (raqamlarni /qoidalar ko'rsatadi)")
        return
    try:
        removed = brain.delete_lesson(int(context.args[0]) - 1)
    except IndexError:
        await update.message.reply_text("Bunday raqamli qoida yo'q.")
        return
    await update.message.reply_text(f"O'chirildi: {removed}")


@owner_only
async def sozlamalar_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if responder is None:
        await update.message.reply_text("Komentga javob tizimi o'chiq.")
        return
    await update.message.reply_text(
        "⚙️ Limitni tejash qoidalari\n\n" + brain.policy_text(responder.status())
        + "\n\nO'zgartirish uchun oddiy so'z bilan yozing, masalan:\n"
        "• belgilashlarga kuniga 50 ta javob yetadi\n"
        "• maqtovlarga javob yozma\n"
        "• bir odamga kuniga 2 ta javob yozsang bo'ladi\n"
        "• emoji komentlarning yarmiga javob yoz\n"
        "• limitni 600 qil")


@owner_only
async def xatolar_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = audit.recent(15, kinds=("log",))
    inf = audit.info()
    head = ("🛰 Kuzatuv: " + (f"{inf['repo']} ga yuborilyapti" if inf["configured"] else "GitHub'ga ulanmagan")
            + (f"\n⚠️ {inf['error']}" if inf["error"] else "") + "\n\n")
    if not items:
        await update.message.reply_text(head + "Oxirgi paytda xato yozilmagan ✅")
        return
    rows = [f"{datetime.fromtimestamp(i['t'], catalog.TASHKENT):%d.%m %H:%M} [{i.get('src')}] {i.get('msg', '')[:250]}"
            for i in items]
    await send_long(context.bot, update.effective_chat.id, head + "Oxirgi xatolar:\n\n" + "\n\n".join(rows))


@owner_only
async def kuzatuv_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    base = (webapp.webapp_url() or "").split("/app")[0].rstrip("/")
    if not base:
        await update.message.reply_text("Server manzili topilmadi (mini ilova sozlanmagan).")
        return
    new = bool(context.args and context.args[0].lower() in ("yangi", "new"))
    key = audit.get_key(new=new)
    await update.message.reply_text(
        "🛰 Kuzatuv havolasi. Uni faqat Claude'ga yuboring: u shu orqali Jarvis qarorlari va xatolarini "
        "o'qib, tuzatib boradi.\n\n" + f"{base}/audit/{key}" +
        "\n\nHavola begona qo'lga tushsa: /kuzatuv yangi (eskisi darhol ishlamay qoladi).",
        disable_web_page_preview=True)


@owner_only
async def jurnal_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = brain.journal_text(25)
    await send_long(context.bot, update.effective_chat.id,
                    "📒 Jarvis oxirgi ishlari:\n" + (text or "Hali yozuv yo'q."))


# ---------- Instagram tahlili ----------

@owner_only
async def insta_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_instagram(update):
        return
    limit = 12
    if context.args and context.args[0].isdigit():
        limit = max(3, min(30, int(context.args[0])))
    chat_id = update.effective_chat.id
    await update.message.reply_text(f"⏳ Oxirgi {limit} ta kontent yig'ilmoqda, 1-2 daqiqa...")
    await send_account_report(context.bot, chat_id, limit)


async def send_account_report(bot, chat_id, limit=12, title=None):
    try:
        data = await asyncio.to_thread(analytics.collect, limit)
    except InstagramError as e:
        await bot.send_message(chat_id=chat_id, text=ig_error_text(e))
        return False

    if not data["posts"]:
        await bot.send_message(chat_id=chat_id, text="Akkauntda hali post topilmadi.")
        return False

    await send_long(bot, chat_id, analytics.summary_text(data, title))
    await bot.send_chat_action(chat_id=chat_id, action="typing")
    analysis = await asyncio.to_thread(analytics.ai_account_analysis, jarvis.ai_provider, data)
    await send_long(bot, chat_id, "🧠 Jarvis tahlili\n\n" + analysis)
    return True


@owner_only
async def post_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_instagram(update):
        return
    if not context.args:
        await update.message.reply_text(
            "Qaysi post? Masalan:\n/post 1 — eng oxirgi post\n/post 3 — oxiridan uchinchisi\n"
            "/post https://www.instagram.com/reel/... — havola bo'yicha"
        )
        return
    ref = context.args[0]
    chat_id = update.effective_chat.id
    await update.message.reply_text("⏳ Post va komentlar yig'ilmoqda...")
    try:
        post = await asyncio.to_thread(analytics.find_post, ref)
        if post is None:
            await update.message.reply_text(
                "Post topilmadi. Raqam (1 = eng oxirgisi) yoki oxirgi 50 ta postdan birining havolasini yuboring."
            )
            return
        data = await asyncio.to_thread(analytics.collect_post, post)
    except InstagramError as e:
        await update.message.reply_text(ig_error_text(e))
        return

    await send_long(context.bot, chat_id, analytics.post_summary_text(data))
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    analysis = await asyncio.to_thread(analytics.ai_post_analysis, jarvis.ai_provider, data)
    await send_long(context.bot, chat_id, "🧠 Jarvis tahlili\n\n" + analysis)


@owner_only
async def stories_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_instagram(update):
        return

    def load():
        followers = ig.get_profile().get("followers_count")
        items = ig.get_stories()
        out = []
        for s in items:
            try:
                s["insights"] = ig.get_media_insights(s)
            except InstagramError as e:
                if e.fatal or e.rate_limited:
                    raise
                s["insights"] = {}
            out.append(analytics.post_metrics(s, followers))
        return out

    try:
        stories = await asyncio.to_thread(load)
    except InstagramError as e:
        await update.message.reply_text(ig_error_text(e))
        return
    await send_long(context.bot, update.effective_chat.id, analytics.stories_text(stories))


@owner_only
async def igtoken_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Yangi Instagram tokenni Telegram orqali qabul qiladi (Railway'ga kirmasdan)."""
    msg = update.message
    if not context.args:
        await msg.reply_text(
            "Yangi Instagram tokenni shunday yuboring:\n/igtoken IGAA...\n\n"
            "Xabaringiz xavfsizlik uchun darhol o'chiriladi."
        )
        return
    token = context.args[0]
    # Token chatda qolmasin
    try:
        await msg.delete()
    except Exception:
        logger.warning("Token yozilgan xabarni o'chirib bo'lmadi.")
    chat_id = update.effective_chat.id
    try:
        username = await asyncio.to_thread(ig.set_token, token)
    except InstagramError as e:
        await context.bot.send_message(chat_id=chat_id, text=f"❌ {e}\nMeta'dan yangi token olib, qayta yuboring.")
        return
    sched = _load_scheduler()
    sched.get("alerts", {}).pop("ig_fatal", None)
    _save_scheduler(sched)
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"✅ Instagram ulandi: @{username}\nToken saqlandi va o'zi yangilanib turadi. Endi /insta yozib ko'ring.",
    )


@owner_only
async def reklamatoken_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reklama kabinetini o'qish tokenini Telegram orqali qabul qiladi."""
    msg = update.message
    if not context.args:
        await msg.reply_text(
            "Reklama kabinetini o'qish tokenini shunday yuboring:\n/reklamatoken EAA...\n\n"
            f"Kabinet: {ads_api.account_id().replace('act_', '')}\n"
            "Xabaringiz xavfsizlik uchun darhol o'chiriladi."
        )
        return
    token = context.args[0]
    try:
        await msg.delete()
    except Exception:
        logger.warning("Token yozilgan xabarni o'chirib bo'lmadi.")
    chat_id = update.effective_chat.id
    try:
        name = await asyncio.to_thread(ads_api.set_token, token)
    except ads_api.AdsError as e:
        await context.bot.send_message(chat_id=chat_id, text=f"❌ {e}")
        return
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"✅ Reklama kabineti ulandi: {name}\nEndi /reklama yozing yoki HUD'da natijalarni ko'ring.")


@owner_only
async def reklama_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reklama natijasi: /reklama, /reklama bugun, /reklama kecha, /reklama 30"""
    if not ads_api.is_configured():
        await update.message.reply_text(
            "Reklama kabineti hali ulanmagan. Token olib, /reklamatoken EAA... qilib yuboring.")
        return
    arg = (context.args[0].lower() if context.args else "7")
    period = {"bugun": "today", "kecha": "yesterday", "30": "30", "oy": "30"}.get(arg, "7")
    try:
        data = await asyncio.to_thread(ads_api.get_summary, period)
    except ads_api.AdsError as e:
        await update.message.reply_text(f"❌ {e}")
        return
    await update.message.reply_text(ads_api.summary_text(data))


@owner_only
async def limit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if responder is None:
        await update.message.reply_text("Komentlarga javob tizimi o'chiq.")
        return
    if context.args and context.args[0].isdigit():
        n = responder.set_day_limit(int(context.args[0]))
        await update.message.reply_text(f"✅ Kunlik avtomatik javoblar limiti: {n}")
        return
    st = responder.status()
    await update.message.reply_text(
        f"Bugun: {st['sent_today']} / {st['limit_day']}\nO'zgartirish: /limit 300 (10 dan 1000 gacha)")


@owner_only
async def groqkey_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Groq kalitini Telegram orqali qabul qiladi (Railway'ga kirmasdan)."""
    msg = update.message
    if not context.args:
        await msg.reply_text(
            "Groq kalitini shunday yuboring:\n/groqkey gsk_...\n\n"
            "Kalitni console.groq.com → API Keys bo'limidan olasiz. Xabaringiz darhol o'chiriladi."
        )
        return
    key = context.args[0].strip()
    try:
        await msg.delete()
    except Exception:
        logger.warning("Kalit yozilgan xabarni o'chirib bo'lmadi.")
    chat_id = update.effective_chat.id
    try:
        n = await asyncio.to_thread(check_groq_key, key)
    except GroqError as e:
        await context.bot.send_message(chat_id=chat_id, text=f"❌ {e}. console.groq.com'dan kalitni qayta nusxalang.")
        return
    await asyncio.to_thread(save_groq_key, key)
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"✅ Groq ulandi ({n} ta model). Endi Gemini limiti tugasa, Jarvis avtomatik Groq'ga o'tadi.\n/diag bilan tekshiring.",
    )


@owner_only
async def diag_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gemini va Instagram holatini tekshiradi."""
    await update.message.reply_text("⏳ Tekshirilmoqda (1 daqiqagacha)...")
    lines = ["🔧 Diagnostika"]
    ai = jarvis.ai_provider
    if hasattr(ai, "diagnose"):
        report = await asyncio.to_thread(ai.diagnose)
        if "status" in report or "tested" in report:
            report = {getattr(ai, "name", "AI"): report}
        for name, d in report.items():
            if name == "Groq" and not d.get("configured"):
                lines.append("\nGroq (zaxira): ⚪ ulanmagan — /groqkey bilan kalit yuboring")
                continue
            ok = d.get("status") == 200
            lines.append(f"\n{name}: {'✅ ishlayapti' if ok else '❌ xato'}")
            if d.get("working"):
                lines.append(f"Model: {d['working']}")
            if d.get("tested"):
                lines.append("Sinov:\n" + "\n".join("  " + t for t in d["tested"]))
            if d.get("error"):
                lines.append(f"Xabar: {d['error']}")
    else:
        lines.append(f"\nAI provider: {type(ai).__name__} (Gemini emas)")

    if ig.is_configured():
        try:
            p = await asyncio.to_thread(ig.get_profile)
            lines.append(f"\nInstagram: ✅ @{p.get('username')} ({p.get('followers_count')} obunachi)")
        except InstagramError as e:
            lines.append(f"\nInstagram: ❌ {e}")
    else:
        lines.append("\nInstagram: ulanmagan")
    if responder:
        lines.append(f"Komentlarga javob rejimi: {responder.mode}")
    await send_long(context.bot, update.effective_chat.id, "\n".join(lines))


# ---------- komentlarga javob ----------

MODE_TEXT = {
    "on": "YONIQ — oddiy komentlarga o'zi javob yozadi, raqobatchi va so'kingan komentlarni sizga yuboradi",
    "test": "TEST — hech kimga yozmaydi, faqat sizga qanday javob berishini ko'rsatadi",
    "off": "O'CHIQ — komentlar tekshirilmaydi",
}


@owner_only
async def autoreply_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if responder is None:
        await update.message.reply_text(
            "Komentlarga javob uchun Gemini kerak. Railway Variables'da AI_PROVIDER=gemini bo'lsin."
        )
        return

    if context.args:
        mode = context.args[0].lower()
        if mode not in MODE_TEXT:
            await update.message.reply_text("Rejimlar: /autoreply on | /autoreply test | /autoreply off")
            return
        responder.set_mode(mode)
        logger.info("Autoreply rejimi o'zgardi: %s", mode)

    st = responder.status()
    lines = [
        f"Komentlarga javob: {MODE_TEXT[st['mode']]}",
        f"Bugun avtomatik yuborildi: {st['sent_today']} / {st['limit_day']}",
        f"Ruxsat kutayotgan komentlar: {st['pending']}",
        f"Tekshirish oralig'i: har {CHECK_EVERY_SEC} soniyada",
    ]
    lines += last_run_lines(st.get("last_run"))
    if not ig.is_configured():
        lines.append("\n⚠️ Instagram hali ulanmagan (INSTAGRAM_ACCESS_TOKEN yo'q).")
    lines.append("\nRejimni o'zgartirish: /autoreply on | /autoreply test | /autoreply off")
    await update.message.reply_text("\n".join(lines))


def last_run_lines(r):
    if not r:
        return ["\nOxirgi tekshiruv: hali bo'lmagan (/checknow bilan hozir tekshiring)"]
    return [
        f"\nOxirgi tekshiruv ({r['time']}): {r['posts']} post, {r['with_comments']} tasida komment, "
        f"jami {r['comments']} komment",
        f"• yangi: {r['new']} | 24 soatdan eski: {r['old']} | avval ko'rilgan: {r['known']} | "
        f"siz javob bergan: {r['already'] + r['mine']}",
        f"• yuborildi: {r['sent']} | ruxsatga: {r['approval']} | test: {r['test']} | spam: {r['skipped']} | "
        f"xato: {r['ai_errors'] + r['send_errors']}",
    ]


@owner_only
async def checknow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Komentlarni hoziroq tekshiradi va natijani ko'rsatadi."""
    if responder is None:
        await update.message.reply_text("Komentlarga javob tizimi o'chiq (Gemini kerak).")
        return
    if responder.mode == "off":
        await update.message.reply_text("Avtojavob O'CHIQ. Avval /autoreply on yoki /autoreply test qiling.")
        return
    await update.message.reply_text("⏳ Komentlar tekshirilmoqda...")
    chat_id = update.effective_chat.id
    try:
        events = await asyncio.to_thread(responder.run_once)
    except InstagramError as e:
        await update.message.reply_text(ig_error_text(e))
        return
    except AIError as e:
        await update.message.reply_text(f"AI xatosi: {e}")
        return
    finally:
        await send_unsent_approvals(context.bot, chat_id)
    for ev in events:
        try:
            await notify_event(context.bot, chat_id, ev)
        except Exception:
            logger.exception("Voqeani yuborib bo'lmadi.")
    await update.message.reply_text("\n".join(["✅ Tekshirildi"] + last_run_lines(responder.last_run)))


def approval_text(item):
    lines = [
        f"⚠️ Ruxsat kerak: {item.get('reason') or 'tekshiring'}",
        f"Kategoriya: {CATEGORY_NAMES.get(item.get('category'), item.get('category'))}",
    ]
    if item.get("permalink"):
        lines.append(f"Post: {item['permalink']}")
    lines.append(f"\n@{item.get('username', '?')}: «{item.get('text', '')}»")
    lines.append("\nJarvis taklifi: " + (item.get("reply") or "(tayyor javob yo'q)"))
    lines.append("\nO'zingiz boshqa javob yozmoqchi bo'lsangiz, shu xabarga Reply qilib yozing.")
    return "\n".join(lines)


def approval_keyboard(item):
    cid = item["cid"]
    first_row = []
    if item.get("reply"):
        first_row.append(InlineKeyboardButton("✅ Yuborish", callback_data=f"ar|ok|{cid}"))
    first_row.append(InlineKeyboardButton("🙈 Yashirish", callback_data=f"ar|hide|{cid}"))
    return InlineKeyboardMarkup([
        first_row,
        [InlineKeyboardButton("❌ Javobsiz qoldirish", callback_data=f"ar|no|{cid}")],
    ])


async def send_approval(bot, owner, item):
    msg = await bot.send_message(
        chat_id=owner,
        text=approval_text(item),
        reply_markup=approval_keyboard(item),
        disable_web_page_preview=True,
    )
    responder.set_message_id(item["cid"], msg.message_id)


# ---------- xotira ----------

def memory_text():
    mem = catalog.get_memory()
    if not mem:
        return ("Xotira bo'sh.\nQo'shish: /eslab Rolikli krossovka — 389 000 so'm, 31-38 o'lcham\n"
                "yoki shunchaki yozing: eslab qol: ...")
    return ("🧠 Jarvis xotirasi:\n" + "\n".join(f"{i}. {m}" for i, m in enumerate(mem, 1))
            + "\n\nQo'shish: /eslab <ma'lumot>\nO'chirish: /unut <raqam>")


async def remember(update, text):
    try:
        mem = catalog.add_memory(text)
    except ValueError:
        await update.message.reply_text("Nimani eslab qolay? Masalan: /eslab Rolikli krossovka — 389 000 so'm")
        return
    await update.message.reply_text(
        f"🧠 Eslab qoldim ({len(mem)} ta yozuv). Komment va direkt javoblarida shundan foydalanaman.\nKo'rish: /xotira")


@owner_only
async def eslab_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await remember(update, " ".join(context.args or []))


@owner_only
async def xotira_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_long(context.bot, update.effective_chat.id, memory_text())


@owner_only
async def unut_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Qaysi yozuvni? Masalan: /unut 2 (raqamlarni /xotira ko'rsatadi)")
        return
    try:
        removed = catalog.delete_memory(int(context.args[0]) - 1)
    except ValueError as e:
        await update.message.reply_text(str(e))
        return
    await update.message.reply_text(f"O'chirildi: {removed}")


# ---------- direkt yozishmalar ----------

CHAT_MODE_TEXT = {
    "approve": "TASDIQ — Jarvis javob tayyorlaydi, siz tasdiqlagach yuboriladi",
    "auto": "AVTO — Jarvis o'zi javob beradi va sotuvga olib boradi; buyurtma yoki muammo bo'lsa sizni xabardor qiladi",
    "off": "O'CHIQ — direktga yozganlarga javob berilmaydi",
}


def chat_approval_text(item):
    lines = ["💬 Direktga xabar keldi"]
    if item.get("reason"):
        lines.append(f"⚠️ {item['reason']}")
    lines.append(f"Mijoz: @{item.get('username') or '?'}")
    if item.get("topic"):
        lines.append(f"So'radi: {item['topic']}")
    lines.append(f"\nYozishma:\n{item.get('history', '')}")
    lines.append("\nJarvis javobi:\n" + (item.get("text") or "(tayyor matn yo'q)"))
    lines.append("\nBoshqa javob yozmoqchi bo'lsangiz, shu xabarga Reply qilib yozing.")
    return "\n".join(lines)[:3900]


async def send_chat_approval(bot, owner, item):
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("💬 Yuborish", callback_data=f"dc|ok|{item['key']}"),
        InlineKeyboardButton("❌ Javobsiz", callback_data=f"dc|no|{item['key']}"),
    ]])
    msg = await bot.send_message(chat_id=owner, text=chat_approval_text(item), reply_markup=kb,
                                 disable_web_page_preview=True)
    chat.set_message_id(item["key"], msg.message_id)


async def send_unsent_chat_approvals(bot, owner):
    for item in chat.get_pending():
        if item.get("msg_id"):
            continue
        try:
            await send_chat_approval(bot, owner, item)
        except Exception:
            logger.exception("Direkt tasdig'ini yuborib bo'lmadi.")


async def notify_chat_event(bot, owner, ev):
    if ev["type"] == "chat_sent":
        text = (f"💬 Direktda javob berildi: @{ev.get('username')}\n"
                + (f"{'' if ev['reason'].startswith('🛒') else '⚠️ '}{ev['reason']}\n" if ev.get("reason") else "")
                + f"Mijoz: «{ev['message']}»\n→ {ev['text']}")
        await bot.send_message(chat_id=owner, text=text, disable_notification=not ev.get("reason"),
                               disable_web_page_preview=True)
    elif ev["type"] == "chat_error":
        await bot.send_message(chat_id=owner, text=f"⚠️ @{ev.get('username')} ga direkt javobi yuborilmadi: {ev.get('error')}")


@owner_only
async def chat_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        _, action, key = query.data.split("|", 2)
    except ValueError:
        await query.answer()
        return
    await query.answer("Bajarilmoqda...")
    try:
        result = await asyncio.to_thread(chat.approve if action == "ok" else chat.reject, key)
    except InstagramError as e:
        result = ig_error_text(e)
    try:
        await query.edit_message_text((query.message.text or "") + "\n\n— " + result, disable_web_page_preview=True)
    except Exception:
        await context.bot.send_message(chat_id=query.message.chat_id, text=result)


@owner_only
async def yozishma_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if chat is None:
        await update.message.reply_text("Bu funksiya uchun Gemini kerak.")
        return
    names = {"tasdiq": "approve", "approve": "approve", "auto": "auto", "avto": "auto", "off": "off"}
    if context.args:
        mode = names.get(context.args[0].lower())
        if not mode:
            await update.message.reply_text("Rejimlar: /yozishma tasdiq | /yozishma avto | /yozishma off")
            return
        chat.set_mode(mode)
    st = chat.status()
    lines = [f"Direkt yozishmalar: {CHAT_MODE_TEXT[st['mode']]}", f"Tasdiq kutayotgan: {st['pending']}"]
    if st.get("last_run"):
        r = st["last_run"]
        lines.append(f"Oxirgi tekshiruv ({r['time']}): {r['convs']} yozishma, yangi {r['new']}, "
                     f"yuborildi {r['sent']}, tasdiqqa {r['approval']}")
    lines.append("\nO'zgartirish: /yozishma tasdiq | /yozishma avto | /yozishma off")
    await update.message.reply_text("\n".join(lines))


def dm_approval_text(item):
    lines = ["📩 Direkt xabar tayyor, tasdiqlaysizmi?"]
    if item.get("reason"):
        lines.append(f"⚠️ {item['reason']}")
    lines.append(f"Mijoz: @{item.get('username', '?')}")
    lines.append(f"Komment: «{item.get('comment', '')}»")
    if item.get("topic"):
        lines.append(f"So'radi: {item['topic']}")
    post = f"Post: {item.get('post_time', '')}"
    if item.get("title"):
        post += f" · {item['title']}"
    elif item.get("caption"):
        post += f" · {item['caption'][:60]}"
    lines.append(post)
    if item.get("permalink"):
        lines.append(item["permalink"])
    lines.append("\n" + (item.get("text") or "(tayyor matn yo'q — Reply qilib o'zingiz yozing)"))
    lines.append("\nBoshqa matn yubormoqchi bo'lsangiz, shu xabarga Reply qilib yozing.")
    return "\n".join(lines)


async def send_dm_approval(bot, owner, item):
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("📩 Yuborish", callback_data=f"dm|ok|{item['cid']}"),
        InlineKeyboardButton("❌ Yubormaslik", callback_data=f"dm|no|{item['cid']}"),
    ]])
    msg = await bot.send_message(chat_id=owner, text=dm_approval_text(item), reply_markup=kb,
                                 disable_web_page_preview=True)
    responder.set_dm_message_id(item["cid"], msg.message_id)


async def send_unsent_approvals(bot, owner):
    for item in responder.get_pending():
        if item.get("msg_id"):
            continue
        try:
            await send_approval(bot, owner, item)
        except Exception:
            logger.exception("Ruxsat so'rovini yuborib bo'lmadi | comment=%s", item["cid"])
    for item in responder.get_dm_pending():
        if item.get("msg_id"):
            continue
        try:
            await send_dm_approval(bot, owner, item)
        except Exception:
            logger.exception("Direkt tasdig'ini yuborib bo'lmadi | comment=%s", item["cid"])


@owner_only
async def pending_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if responder is None:
        await update.message.reply_text("Komentlarga javob tizimi o'chiq (Gemini kerak).")
        return
    items = responder.get_pending()
    if not items:
        await update.message.reply_text("Ruxsat kutayotgan komment yo'q.")
        return
    for item in items[:20]:
        await send_approval(context.bot, update.effective_chat.id, item)


@owner_only
async def dm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        _, action, cid = query.data.split("|", 2)
    except ValueError:
        await query.answer()
        return
    await query.answer("Bajarilmoqda...")
    try:
        if action == "ok":
            result = await asyncio.to_thread(responder.approve_dm, cid)
        else:
            result = await asyncio.to_thread(responder.reject_dm, cid)
    except InstagramError as e:
        result = ig_error_text(e)
    try:
        await query.edit_message_text((query.message.text or "") + "\n\n— " + result,
                                      disable_web_page_preview=True)
    except Exception:
        await context.bot.send_message(chat_id=query.message.chat_id, text=result)


@owner_only
async def direkt_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if responder is None:
        await update.message.reply_text("Komentlarga javob tizimi o'chiq (Gemini kerak).")
        return
    names = {"tasdiq": "approve", "approve": "approve", "auto": "auto", "avto": "auto", "off": "off", "ochiq": "off"}
    if context.args:
        mode = names.get(context.args[0].lower())
        if not mode:
            await update.message.reply_text("Rejimlar: /direkt tasdiq | /direkt avto | /direkt off")
            return
        responder.set_dm_mode(mode)
    texts = {
        "approve": "TASDIQ — narx so'ragan mijozga direkt xabar tayyorlanadi, siz tasdiqlagach yuboriladi",
        "auto": "AVTO — direkt xabar darhol yuboriladi; narx aniq bo'lmasa, mijozdan aniqlashtiruvchi xabar ketadi",
        "off": "O'CHIQ — direktga yozilmaydi",
    }
    n = len(catalog.load()["posts"])
    await update.message.reply_text(
        f"Direkt rejimi: {texts[responder.dm_mode]}\nMa'lumot kiritilgan postlar: {n} ta."
        + "\nPostlarga narx va ma'lumotni Jarvis HUD → POSTLAR bo'limida kiritasiz."
        + "\n\nO'zgartirish: /direkt tasdiq | /direkt avto | /direkt off"
    )


@owner_only
async def approval_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        _, action, cid = query.data.split("|", 2)
    except ValueError:
        await query.answer()
        return
    await query.answer("Bajarilmoqda...")

    if responder is None:
        result = "Komentlarga javob tizimi o'chiq."
    else:
        try:
            if action == "ok":
                result = await asyncio.to_thread(responder.approve, cid)
            elif action == "hide":
                result = await asyncio.to_thread(responder.hide, cid)
            else:
                result = await asyncio.to_thread(responder.reject, cid)
        except InstagramError as e:
            result = ig_error_text(e)

    try:
        await query.edit_message_text(
            (query.message.text or "") + "\n\n— " + result,
            disable_web_page_preview=True,
        )
    except Exception:
        await context.bot.send_message(chat_id=query.message.chat_id, text=result)


# ---------- fon jarayoni ----------

def _scheduler_path():
    return os.path.join(config.DATA_DIR, "scheduler.json")


def _load_scheduler():
    try:
        with open(_scheduler_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_scheduler(data):
    try:
        os.makedirs(config.DATA_DIR, exist_ok=True)
        with open(_scheduler_path(), "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError:
        logger.exception("Scheduler holatini saqlab bo'lmadi.")


async def alert_owner(bot, owner, key, text, every_hours=12):
    """Bir xil ogohlantirishni har every_hours soatda ko'pi bilan bir marta yuboradi."""
    sched = _load_scheduler()
    alerts = sched.setdefault("alerts", {})
    if time.time() - alerts.get(key, 0) < every_hours * 3600:
        return
    alerts[key] = time.time()
    _save_scheduler(sched)
    try:
        await bot.send_message(chat_id=owner, text=text)
    except Exception:
        logger.exception("Ogohlantirishni yuborib bo'lmadi.")


async def notify_event(bot, owner, ev):
    who = f"@{ev.get('username', '?')}: «{ev.get('text', '')[:300]}»"
    if ev["type"] == "test":
        text = (
            f"🧪 TEST — shunday javob berardim (yuborilmadi)\n"
            f"Kategoriya: {CATEGORY_NAMES.get(ev['category'], ev['category'])}\n\n"
            f"{who}\n→ {ev['reply']}\n\n{ev.get('permalink', '')}"
        )
    elif ev["type"] == "sent":
        text = f"✅ Avtomatik javob yozildi\n{who}\n→ {ev['reply']}\n{ev.get('permalink', '')}".rstrip()
    elif ev["type"] == "dm_approval":
        return  # send_unsent_approvals() yuboradi
    elif ev["type"] == "dm_sent":
        text = (f"📩 Direktga yuborildi: @{ev.get('username')}\n"
                + (f"⚠️ {ev['reason']}\n" if ev.get("reason") else "")
                + f"Komment: «{ev.get('comment', '')}» · post {ev.get('post_time', '')}\n\n{ev['text']}")
    elif ev["type"] == "dm_error":
        text = f"⚠️ @{ev.get('username')} ga direkt yuborilmadi: {ev.get('error')}"
        await bot.send_message(chat_id=owner, text=text, disable_web_page_preview=True)
        return
    else:
        return
    if ev["type"] == "test" and ev.get("dm"):
        d = ev["dm"]
        text += (f"\n\n📩 Direktga shunday yozardim (post {d.get('post_time', '')}"
                 + (f", ⚠️ {d['reason']}" if d.get("reason") else "") + f"):\n{d.get('text') or '—'}")
    await bot.send_message(chat_id=owner, text=text, disable_notification=True,
                           disable_web_page_preview=True)


async def maybe_daily_jobs(bot, owner):
    sched = _load_scheduler()
    now = datetime.now(analytics.TASHKENT)
    today = now.strftime("%Y-%m-%d")

    # Har kuni obunachilar sonini yozib boramiz (o'sishni ko'rish uchun)
    if sched.get("followers_day") != today:
        profile = await asyncio.to_thread(ig.get_profile)
        analytics.record_followers(profile.get("followers_count"))
        sched["followers_day"] = today
        _save_scheduler(sched)

    # Haftalik hisobot: dushanba, WEEKLY_REPORT_HOUR dan keyin
    if os.environ.get("WEEKLY_REPORT", "1") == "0":
        return
    hour = int(os.environ.get("WEEKLY_REPORT_HOUR", "10") or 10)
    week = f"{now.isocalendar()[0]}-W{now.isocalendar()[1]}"
    if now.weekday() == 0 and now.hour >= hour and sched.get("weekly") != week:
        sched["weekly"] = week
        _save_scheduler(sched)
        await send_account_report(bot, owner, 12, title="📅 Haftalik Instagram hisobot")


async def instagram_tick(app):
    owner = get_owner_id()
    if owner is None or not ig.is_configured():
        return
    bot = app.bot
    try:
        await asyncio.to_thread(ig.refresh_token_if_needed)
        if responder is not None:
            st = responder.status()
            st["chat_mode"] = chat.mode if chat is not None else None
            st["lessons"] = brain.get_lessons()
            await asyncio.to_thread(audit.ship, st)

        try:
            await asyncio.to_thread(followers_live.sample, 110)
        except InstagramError as e:
            if e.fatal:
                raise
            logger.warning("Obunachilar sonini o'qib bo'lmadi: %s", e)

        try:
            await asyncio.to_thread(reel_sources.snapshot)
        except InstagramError as e:
            if e.fatal:
                raise
            logger.warning("Reels ko'rishlarini o'qib bo'lmadi: %s", e)
        except Exception as e:  # noqa: BLE001 - qo'shimcha statistika asosiy ishni to'xtatmasin
            logger.warning("Reels manbasi: %s", e)

        if responder is not None:
            try:
                await asyncio.to_thread(responder.apply_owner_setup)
            except InstagramError as e:
                if e.fatal:
                    raise
                logger.warning("Egasining sozlamasini qo'llab bo'lmadi: %s", e)

        if responder is not None and responder.mode != "off":
            try:
                events = await asyncio.to_thread(responder.run_once)
            finally:
                await send_unsent_approvals(bot, owner)
            for ev in events:
                brain.log_event(ev)
                try:
                    await notify_event(bot, owner, ev)
                except Exception:
                    logger.exception("Voqeani yuborib bo'lmadi.")

        if chat is not None and chat.mode != "off":
            try:
                chat_events = await asyncio.to_thread(chat.run_once)
            except InstagramError as e:
                if e.fatal or e.rate_limited:
                    raise
                chat_events = []
                await alert_owner(
                    bot, owner, "chat_error",
                    f"⚠️ Direkt yozishmalarni o'qib bo'lmadi: {e}\nToken xabarlar ruxsati "
                    "(instagram_business_manage_messages) bilan olinganini tekshiring.", every_hours=24)
            finally:
                await send_unsent_chat_approvals(bot, owner)
            for ev in chat_events:
                brain.log_event(ev)
                try:
                    await notify_chat_event(bot, owner, ev)
                except Exception:
                    logger.exception("Voqeani yuborib bo'lmadi.")

        await maybe_daily_jobs(bot, owner)

    except InstagramError as e:
        if e.fatal:
            await alert_owner(bot, owner, "ig_fatal", "⚠️ " + ig_error_text(e))
        else:
            logger.warning("Instagram vaqtinchalik xatosi: %s", e)
    except AIError as e:
        if e.fatal:
            await alert_owner(
                bot, owner, "gemini_fatal",
                f"⚠️ AI ishlamayapti ({e}). /diag bilan tekshiring.",
            )
        else:
            logger.warning("Gemini vaqtinchalik xatosi: %s", e)


async def instagram_loop(app):
    await asyncio.sleep(20)
    while True:
        try:
            await instagram_tick(app)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Instagram fon jarayonida kutilmagan xato.")
        await asyncio.sleep(CHECK_EVERY_SEC)


def creator_admin_ids():
    """Blogerlar botining administratorlari: Jarvis egasi + CREATOR_ADMIN_IDS (vergul bilan)."""
    ids = {get_owner_id()}
    for part in (os.environ.get("CREATOR_ADMIN_IDS") or "").split(","):
        if part.strip().lstrip("-").isdigit():
            ids.add(int(part.strip()))
    return {i for i in ids if i}


async def post_init(app):
    try:
        await app.bot.set_my_commands([
            BotCommand("app", "Jarvis HUD mini ilovasi"),
            BotCommand("insta", "Instagram to'liq tahlili"),
            BotCommand("post", "Bitta postni chuqur tahlil"),
            BotCommand("stories", "Faol storylar statistikasi"),
            BotCommand("reklama", "Reklama (target) natijasi"),
            BotCommand("yt", "YouTube video SEO (sarlavha, teglar)"),
            BotCommand("ytholat", "YouTube holati"),
            BotCommand("autoreply", "Komentlarga javob rejimi"),
            BotCommand("pending", "Ruxsat kutayotgan komentlar"),
            BotCommand("direkt", "Komentdan keyingi direkt"),
            BotCommand("yozishma", "Direktga yozganlarga javob"),
            BotCommand("xotira", "Jarvis xotirasi (mahsulot va narxlar)"),
            BotCommand("checknow", "Komentlarni hozir tekshirish"),
            BotCommand("script", "Video ssenariysi"),
            BotCommand("hook", "Hook variantlari"),
            BotCommand("caption", "Instagram caption"),
            BotCommand("ideas", "Kontent g'oyalari"),
            BotCommand("qoidalar", "Jarvis o'rgangan qoidalar"),
            BotCommand("jurnal", "Jarvis oxirgi ishlari"),
            BotCommand("sozlamalar", "Limitni tejash qoidalari"),
            BotCommand("xatolar", "Oxirgi xatolar va kuzatuv holati"),
            BotCommand("start", "Yordam"),
        ])
    except Exception:
        logger.exception("Bot buyruqlar menyusini o'rnatib bo'lmadi.")
    url = webapp.webapp_url()
    if url:
        try:
            await app.bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(text="Jarvis HUD", web_app=WebAppInfo(url=url))
            )
            logger.info("Mini ilova menyu tugmasi o'rnatildi.")
        except Exception:
            logger.exception("Mini ilova tugmasini o'rnatib bo'lmadi.")
    app.bot_data["instagram_task"] = asyncio.create_task(instagram_loop(app))
    # AI marketing agentligi hisobotlari (AGENCY_REPO bo'lsa), aks holda hech narsa qilmaydi
    try:
        app.bot_data["agency_task"] = asyncio.create_task(agency_bridge.loop(app, get_owner_id))
    except Exception:
        logger.exception("Agentlik ko'prigini ishga tushirib bo'lmadi.")
    # Blogerlar boti (Creator Rewards) — CREATOR_BOT_TOKEN bo'lsa ishga tushadi, aks holda hech narsa qilmaydi
    try:
        app.bot_data["creator_app"] = await creator_bot.start_bot(creator_admin_ids)
    except Exception:
        logger.exception("Blogerlar botini ishga tushirib bo'lmadi.")
    logger.info(
        "Instagram fon jarayoni ishga tushdi | instagram=%s | autoreply=%s",
        "ulangan" if ig.is_configured() else "ulanmagan",
        responder.mode if responder else "yo'q",
    )


# Telegramdagi kutilmagan xatolar
async def telegram_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(
        "Telegram update qayta ishlanishida kutilmagan xatolik.",
        exc_info=context.error,
    )


# Telegram tokenini topish
def find_token() -> str:
    token = getattr(config, "TELEGRAM_BOT_TOKEN", "") or ""

    if token.strip():
        return token.strip()

    for name, value in os.environ.items():
        if name.strip().upper() == "TELEGRAM_BOT_TOKEN" and value.strip():
            return value.strip()

    return ""


def build_app(token):
    app = ApplicationBuilder().token(token).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("insta", insta_cmd))
    app.add_handler(CommandHandler("post", post_cmd))
    app.add_handler(CommandHandler("stories", stories_cmd))
    app.add_handler(CommandHandler("autoreply", autoreply_cmd))
    app.add_handler(CommandHandler("pending", pending_cmd))
    app.add_handler(CommandHandler("igtoken", igtoken_cmd))
    app.add_handler(CommandHandler("reklama", reklama_cmd))
    app.add_handler(CommandHandler("reklamatoken", reklamatoken_cmd))
    app.add_handler(CommandHandler("diag", diag_cmd))
    app.add_handler(CommandHandler("groqkey", groqkey_cmd))
    app.add_handler(CommandHandler("limit", limit_cmd))
    app.add_handler(CommandHandler("app", app_cmd))
    app.add_handler(CommandHandler("checknow", checknow_cmd))
    app.add_handler(CommandHandler(JARVIS_COMMANDS, jarvis_command))
    app.add_handler(CallbackQueryHandler(approval_callback, pattern=r"^ar\|"))
    app.add_handler(CallbackQueryHandler(dm_callback, pattern=r"^dm\|"))
    app.add_handler(CommandHandler("direkt", direkt_cmd))
    app.add_handler(CallbackQueryHandler(chat_callback, pattern=r"^dc\|"))
    app.add_handler(CommandHandler("yozishma", yozishma_cmd))
    app.add_handler(CommandHandler("eslab", eslab_cmd))
    app.add_handler(CommandHandler("xotira", xotira_cmd))
    app.add_handler(CommandHandler("unut", unut_cmd))
    app.add_handler(CommandHandler("qoidalar", qoidalar_cmd))
    app.add_handler(CommandHandler("qoidaochir", qoidaochir_cmd))
    app.add_handler(CommandHandler("jurnal", jurnal_cmd))
    app.add_handler(CommandHandler("sozlamalar", sozlamalar_cmd))
    app.add_handler(CommandHandler("xatolar", xatolar_cmd))
    app.add_handler(CommandHandler("kuzatuv", kuzatuv_cmd))
    # YouTube buyruqlari (kalit va video havolalari umumiy matn handleridan oldin ushlanadi)
    youtube_bot.setup(app, owner_only, lambda: jarvis.ai_provider, send_long, get_owner_id)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(telegram_error_handler)
    return app


# Dasturni ishga tushirish
def main():
    token = find_token()

    if not token:
        logger.error(
            "TELEGRAM_BOT_TOKEN topilmadi. "
            "Railway Variables bo'limini tekshiring."
        )
        raise RuntimeError("TELEGRAM_BOT_TOKEN topilmadi.")

    # Mini ilova uchun kontekst (initData imzosi bot tokeni bilan tekshiriladi)
    webapp.init(jarvis, responder, get_owner_id, token, chat)

    # OAuth callback va mini ilova web-serveri
    start_web_server()

    app = build_app(token)
    logger.info("Jarvis Telegram bot ishga tushmoqda...")
    app.run_polling()


if __name__ == "__main__":
    main()
