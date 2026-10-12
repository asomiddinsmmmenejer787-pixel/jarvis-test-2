Jarvis Telegram runner.
Telegram bot va Instagram OAuth callback web-serverini ishga tushiradi.
"""

import asyncio
import logging
import os
import threading

import config
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse

from core.jarvis import Jarvis
from ai.gemini import GeminiProvider
from ai.mock import MockProvider

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
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
logger = logging.getLogger(__name__)


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


# /start command
async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if update.message is None:
        return

    logger.info(
        "START command qabul qilindi | chat_id=%s",
        update.effective_chat.id if update.effective_chat else "unknown",
    )

    await update.message.reply_text(
        "Assalomu alaykum! Men Jarvis — sizning SMM yordamchingizman.\n\n"
        "Buyruqlar:\n"
        "/script <mavzu> — video ssenariysi\n"
        "/hook <mavzu> — hook variantlari\n"
        "/caption <mavzu> — Instagram caption\n"
        "/telegram <mavzu> — Telegram post\n"
        "/ad <mavzu> — reklama ssenariysi\n"
        "/ideas <mavzu> — kontent g'oyalari\n\n"
        "Yoki oddiy tilda savolingizni yozing."
    )


# Oddiy matnli xabarlar
async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if update.message is None or update.effective_chat is None:
        logger.warning("Xabarda message yoki chat ma'lumoti yo'q.")
        return

    user_text = update.message.text

    if not user_text or not user_text.strip():
        logger.info("Bo'sh matn olindi.")
        return

    chat_id = update.effective_chat.id

    logger.info(
        "HANDLE_MESSAGE ISHLADI | chat_id=%s | text_length=%s",
        chat_id,
        len(user_text),
    )

    try:
        await context.bot.send_chat_action(
            chat_id=chat_id,
            action="typing",
        )

        logger.info("Jarvis.handle() ishga tushmoqda.")

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

        logger.info(
            "Jarvis javob tayyorladi | chat_id=%s | length=%s",
            chat_id,
            len(reply),
        )

        # Telegram xabar uzunligi chegarasidan oshmaslik.
        max_length = 4000

        for index in range(0, len(reply), max_length):
            await update.message.reply_text(
                reply[index:index + max_length]
            )

        logger.info(
            "Telegram javobi yuborildi | chat_id=%s",
            chat_id,
        )

    except Exception:
        logger.exception(
            "HANDLE_MESSAGE ichida xatolik | chat_id=%s",
            chat_id,
        )

        try:
            await update.message.reply_text(
                "Kechirasiz, Jarvisda xatolik yuz berdi. "
                "Birozdan so'ng qayta urinib ko'ring."
            )
        except Exception:
            logger.exception(
                "Xatolik haqida Telegram xabarini yuborib bo'lmadi."
            )


# Telegramdagi kutilmagan xatolar
async def telegram_error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
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
        if (
            name.strip().upper() == "TELEGRAM_BOT_TOKEN"
            and value.strip()
        ):
            return value.strip()

    return ""


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

    # Telegram bot
    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", start))

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message,
        )
    )

    app.add_error_handler(telegram_error_handler)

    logger.info("Jarvis Telegram bot ishga tushmoqda...")

    app.run_polling()


if __name__ == "__main__":
    main()
