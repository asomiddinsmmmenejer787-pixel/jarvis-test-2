"""
config.py
---------
.env faylidan barcha sozlamalarni o'qiydi va dastur bo'ylab
bitta joydan foydalanish uchun tayyorlaydi.

Hech qanday API key yoki token bu faylga yozilmaydi —
faqat .env'dan o'qiladi.
"""

import os
from dotenv import load_dotenv

# .env faylini yuklaymiz (agar mavjud bo'lsa)
load_dotenv()

# AI sozlamalari
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")

# Telegram sozlamalari
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

# Qaysi AI provider ishlatilishi: "gemini" yoki "mock"
# Mock rejim — internet yoki API key bo'lmasa ham dasturni sinash uchun
AI_PROVIDER = os.environ.get("AI_PROVIDER", "mock").lower()

# Doimiy ma'lumotlar papkasi (xotira, Instagram holati, token).
# Railway'da Volume ulab, DATA_DIR=/data qilsangiz, deploy'dan keyin ham o'chmaydi.
DATA_DIR = os.environ.get("DATA_DIR", "").strip() or os.path.join(os.path.dirname(__file__), "data")

# Xotira fayli qayerda saqlanadi
MEMORY_DIR = os.path.join(DATA_DIR, "memory")

# Bot egasi (faqat shu odam botdan foydalana oladi). Telegram'da /myid buyrug'i ko'rsatadi.
_owner = os.environ.get("OWNER_CHAT_ID", "").strip()
OWNER_CHAT_ID = int(_owner) if _owner.lstrip("-").isdigit() else None

# Mahsulotlar bazasi qayerda
PRODUCTS_FILE = os.path.join(os.path.dirname(__file__), "data", "products.json")


def validate_config():
    """
    Sozlamalar to'g'ri kiritilganini tekshiradi.
    Agar Gemini tanlangan bo'lsa-yu, key bo'lmasa — foydalanuvchiga aniq xabar beradi.
    """
    if AI_PROVIDER == "gemini" and not GEMINI_API_KEY:
        raise RuntimeError(
            "AI_PROVIDER=gemini tanlangan, lekin GEMINI_API_KEY topilmadi.\n"
            ".env faylida GEMINI_API_KEY qiymatini kiriting, "
            "yoki AI_PROVIDER=mock qilib sinab ko'ring."
        )
