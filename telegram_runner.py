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

import analytics
import instagram_api as ig
from auto_reply import CATEGORY_NAMES, CommentResponder
from instagram_api import InstagramError
from core.jarvis import Jarvis
from ai.gemini import GeminiError, GeminiProvider
from ai.mock import MockProvider

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
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

TELEGRAM_LIMIT = 4000
CHECK_EVERY_SEC = max(60, int(os.environ.get("CHECK_EVERY_SEC", "600") or 600))
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

        return GeminiProvider(
            api_key=api_key,
            model=model,
        )

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
    "Instagram:\n"
    "/insta — oxirgi 12 kontentning to'liq tahlili (/insta 20 — 20 tasi)\n"
    "/post 1 — bitta postni chuqur tahlil (1 = eng oxirgisi, yoki post havolasi)\n"
    "/stories — faol storylar statistikasi\n"
    "/autoreply — komentlarga javob tizimi holati va rejimi\n"
    "/pending — ruxsat kutayotgan komentlar\n"
    "/igtoken <token> — yangi Instagram tokenni ulash\n"
    "/diag — Gemini va Instagram holatini tekshirish\n\n"
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

    # Xabarda Instagram tokeni bo'lsa (qanday formatda bo'lmasin) - ulaymiz, AI'ga yubormaymiz
    token_match = IG_TOKEN_RE.search(user_text)
    if token_match:
        context.args = [token_match.group(0)]
        await igtoken_cmd.__wrapped__(update, context)
        return

    # Ruxsat so'ralgan komentga egasi Reply qilib o'z javobini yozdi
    replied = update.message.reply_to_message
    if responder and replied is not None:
        cid = responder.find_by_message(replied.message_id)
        if cid:
            try:
                result = await asyncio.to_thread(responder.approve, cid, user_text)
            except InstagramError as e:
                result = ig_error_text(e)
            await update.message.reply_text(result)
            return

    await answer_with_jarvis(update, context, user_text)


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
async def diag_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gemini va Instagram holatini tekshiradi."""
    await update.message.reply_text("⏳ Tekshirilmoqda (1 daqiqagacha)...")
    lines = ["🔧 Diagnostika"]
    ai = jarvis.ai_provider
    if hasattr(ai, "diagnose"):
        d = await asyncio.to_thread(ai.diagnose)
        ok = d.get("status") == 200
        lines.append(f"\nGemini: {'✅ ishlayapti' if ok else '❌ xato'}")
        lines.append(f"Model: {d.get('model')} | javob: {d.get('status')} | {d.get('seconds')} s")
        if d.get("error"):
            lines.append(f"Google xabari: {d['error']}")
        if "model_exists" in d:
            lines.append("Model kalitda mavjud: " + ("ha" if d["model_exists"] else "YO'Q"))
        if d.get("flash_models"):
            lines.append("Mavjud flash modellar: " + ", ".join(d["flash_models"]))
        if d.get("models_error"):
            lines.append(f"Modellar ro'yxati olinmadi: {d['models_error']}")
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
        f"Tekshirish oralig'i: har {CHECK_EVERY_SEC // 60} daqiqada",
    ]
    if not ig.is_configured():
        lines.append("\n⚠️ Instagram hali ulanmagan (INSTAGRAM_ACCESS_TOKEN yo'q).")
    lines.append("\nRejimni o'zgartirish: /autoreply on | /autoreply test | /autoreply off")
    await update.message.reply_text("\n".join(lines))


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


async def send_unsent_approvals(bot, owner):
    for item in responder.get_pending():
        if item.get("msg_id"):
            continue
        try:
            await send_approval(bot, owner, item)
        except Exception:
            logger.exception("Ruxsat so'rovini yuborib bo'lmadi | comment=%s", item["cid"])


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
        text = f"✅ Avtomatik javob yozildi\n{who}\n→ {ev['reply']}"
    else:
        return
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

        if responder is not None and responder.mode != "off":
            try:
                events = await asyncio.to_thread(responder.run_once)
            finally:
                await send_unsent_approvals(bot, owner)
            for ev in events:
                try:
                    await notify_event(bot, owner, ev)
                except Exception:
                    logger.exception("Voqeani yuborib bo'lmadi.")

        await maybe_daily_jobs(bot, owner)

    except InstagramError as e:
        if e.fatal:
            await alert_owner(bot, owner, "ig_fatal", "⚠️ " + ig_error_text(e))
        else:
            logger.warning("Instagram vaqtinchalik xatosi: %s", e)
    except GeminiError as e:
        if e.fatal:
            await alert_owner(
                bot, owner, "gemini_fatal",
                f"⚠️ Gemini xatosi ({e}). GEMINI_API_KEY va GEMINI_MODEL'ni tekshiring.",
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


async def post_init(app):
    try:
        await app.bot.set_my_commands([
            BotCommand("insta", "Instagram to'liq tahlili"),
            BotCommand("post", "Bitta postni chuqur tahlil"),
            BotCommand("stories", "Faol storylar statistikasi"),
            BotCommand("autoreply", "Komentlarga javob rejimi"),
            BotCommand("pending", "Ruxsat kutayotgan komentlar"),
            BotCommand("script", "Video ssenariysi"),
            BotCommand("hook", "Hook variantlari"),
            BotCommand("caption", "Instagram caption"),
            BotCommand("ideas", "Kontent g'oyalari"),
            BotCommand("start", "Yordam"),
        ])
    except Exception:
        logger.exception("Bot buyruqlar menyusini o'rnatib bo'lmadi.")
    app.bot_data["instagram_task"] = asyncio.create_task(instagram_loop(app))
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
    app.add_handler(CommandHandler("diag", diag_cmd))
    app.add_handler(CommandHandler(JARVIS_COMMANDS, jarvis_command))
    app.add_handler(CallbackQueryHandler(approval_callback, pattern=r"^ar\|"))
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

    # OAuth callback web-serveri
    start_web_server()

    app = build_app(token)
    logger.info("Jarvis Telegram bot ishga tushmoqda...")
    app.run_polling()


if __name__ == "__main__":
    main()
