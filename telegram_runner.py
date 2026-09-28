"""
telegram_runner.py
-------------------
Jarvis'ni Telegram botga ulaydigan doimiy ishlaydigan skript.
Railway'da shu fayl ishga tushiriladi (main.py emas).

CORE kodga (core/, content/, ai/) hech qanday o'zgartirish kiritilmadi —
bu fayl shunchaki Jarvis'ning "old eshigi", Telegram xabarlarini
Jarvis.handle() ga uzatib, javobni qaytaradi.
"""

import logging
import config
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

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def build_ai_provider():
    if config.AI_PROVIDER == "gemini":
        return GeminiProvider(api_key=config.GEMINI_API_KEY, model=config.GEMINI_MODEL)
    return MockProvider()


# Bitta global Jarvis instance — barcha foydalanuvchilar shu orqali ishlaydi.
# Har bir foydalanuvchi uchun alohida xotira (memory) core/jarvis.py ichida
# session_id orqali ajratiladi (keyingi bosqichda kengaytiriladi).
jarvis = Jarvis(ai_provider=build_ai_provider(), memory_dir=config.MEMORY_DIR)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
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


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    chat_id = update.effective_chat.id
    logger.info(f"[{chat_id}] Kelgan xabar: {user_text}")

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        reply = jarvis.handle(user_text)
    except Exception as e:
        logger.error(f"Xatolik: {e}")
        reply = "Kechirasiz, xatolik yuz berdi. Birozdan so'ng qayta urinib ko'ring."

    await update.message.reply_text(reply)


def main():
    if not config.TELEGRAM_BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN topilmadi. Railway'ning Variables bo'limida "
            "TELEGRAM_BOT_TOKEN qiymatini kiriting."
        )

    app = ApplicationBuilder().token(config.TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Jarvis Telegram bot ishga tushdi...")
    app.run_polling()


if __name__ == "__main__":
    main()
