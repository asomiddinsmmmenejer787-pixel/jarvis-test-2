"""
tests/test_stage1.py
---------------------
Stage 1 uchun asosiy testlar (spec 15-bo'lim).
MockProvider ishlatiladi — internet yoki API key kerak emas.

Ishga tushirish:
    python -m tests.test_stage1
"""

import sys
import os

# tests/ papkasidan loyihaning bosh papkasiga yo'l ochish
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.jarvis import Jarvis
from ai.mock import MockProvider
from integrations.mock_instagram import MockInstagramProvider
from integrations.mock_telegram import MockTelegramProvider


def run_tests():
    ai_provider = MockProvider()
    jarvis = Jarvis(ai_provider=ai_provider, memory_dir="data/memory")

    test_cases = [
        "/script samokat uchun reels ssenariysi",
        "/caption Bolalar Dunyosi uchun Instagram caption",
        "/telegram yangi mahsulot posti",
        "mijoz: samokat narxi qancha?",
        "mijoz: yetkazib berish bormi?",
        "mijoz: menga operator kerak",
    ]

    print("=" * 50)
    print("STAGE 1 TESTLARI (Content Engine)")
    print("=" * 50)

    for i, test_input in enumerate(test_cases, start=1):
        print(f"\n--- Test {i} ---")
        print(f"Kirish: {test_input}")
        result = jarvis.handle(test_input)
        print(f"Chiqish: {result[:150]}...")  # uzun bo'lsa qisqartirib ko'rsatamiz
        assert result, "Javob bo'sh bo'lmasligi kerak!"

    print("\n" + "=" * 50)
    print("MOCK INTEGRATIONS TESTI")
    print("=" * 50)

    ig = MockInstagramProvider()
    tg = MockTelegramProvider()

    print("\nInstagram xabarlari:", ig.get_messages())
    print("Instagram kommentlari:", ig.get_comments())
    ig.send_message("test_user", "Salom!")

    print("\nTelegram xabarlari:", tg.get_messages())
    tg.send_message("test_chat", "Salom!")

    print("\n✅ Barcha testlar muvaffaqiyatli o'tdi!")


if __name__ == "__main__":
    run_tests()
