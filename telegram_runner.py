"""
Jarvis Telegram runner.
Telegram bot va Instagram OAuth callback web-serverini ishga tushiradi.
"""

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


# Instagram OAuth uchun web-server
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
        port = int(os.environ.get("PORT", "8000"))
        uvicorn.run(web, host="0.0.0.0", port=port)

    thread = threading.Thread(
        target=run,
        daemon=True,
        name="jarvis-web-server",
    )
    thread.start()


# AI provider
def build_ai_provider():
    if config.AI_PROVIDER == "gemini":
        return GeminiProvider(
            api_key=config.GEMINI_API_KEY,
            model=config.GEMINI_MODEL,
        )
    return MockProvider()


# Jarvis instance
jarvis = Jarvis(
    ai_provider=build_ai_provider(),
    memory_dir=config.MEMORY_DIR,
)


# Telegram /start
async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if update.message is None:
        return

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


# Oddiy xabarlarni qayta ishlash
async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if update.message is None or update.effective_chat is None:
        return

    user_text = update.message.text
    if not user_text:
        return

    chat_id = update.effective_chat.id
    logger.info("[%s] Yangi xabar qabul qilindi.", chat_id)

    await context.bot.send_chat_action(
        chat_id=chat_id,
        action="typing",
    )

    try:
        reply = jarvis.handle(user_text)
        if not isinstance(reply, str):
            reply = str(reply)
    except Exception:
        logger.exception("Jarvis xabarni qayta ishlashda xatolik berdi.")
        reply = (
            "Kechirasiz, xatolik yuz berdi. "
            "Birozdan so'ng qayta urinib ko'ring."
        )

    # Telegram xabar uzunligi chegarasidan oshmaslik.
    max_length = 4000
    for index in range(0, len(reply), max_length):
        await update.message.reply_text(reply[index:index + max_length])


# Telegram tokenini topish
def find_token() -> str:
    token = getattr(config, "TELEGRAM_BOT_TOKEN", "") or ""
    if token.strip():
        return token.strip()

    for name, value in os.environ.items():
        if name.strip().upper() == "TELEGRAM_BOT_TOKEN" and value.strip():
            return value.strip()

    return ""


# Dasturni ishga tushirish
def main():
    token = find_token()

    if not token:
        names = [
            repr(name)
            for name in os.environ
            if any(
                key in name.upper()
                for key in ("TELEGRAM", "GEMINI", "AI_PROVIDER")
            )
        ]
        logger.error(
            "Tegishli environment variable nomlari: %s",
            names,
        )
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN topilmadi. "
            "Railway Variables bo'limini tekshiring."
        )

    # Web-serverni ishga tushirish
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

    logger.info("Jarvis Telegram bot ishga tushmoqda...")
    app.run_polling()


if __name__ == "__main__":
    main()
