"""
integrations/mock_telegram.py
-------------------------------
Telegram API hali ulanmagan bo'lsa ham, dasturning qolgan qismini
sinash uchun soxta (mock) ma'lumot qaytaruvchi provider.

Real Telegram integratsiyasi keyingi bosqichda integrations/telegram.py
faylida yoziladi — bu fayl bilan bir xil interfeysga ega bo'lgani uchun
CORE kod o'zgarmaydi.
"""

from integrations.base import MessagingProvider
from typing import List, Dict


class MockTelegramProvider(MessagingProvider):
    """Test uchun soxta Telegram xabar provideri."""

    def get_messages(self) -> List[Dict]:
        return [
            {"chat_id": "mock_chat_1", "text": "Bolalar uchun eng mashhur o'yinchoq qaysi?"},
        ]

    def send_message(self, chat_id: str, text: str) -> bool:
        print(f"[MOCK TELEGRAM] -> {chat_id}: {text}")
        return True
