"""
integrations/mock_instagram.py
-------------------------------
Instagram API hali ulanmagan bo'lsa ham, dasturning qolgan qismini
sinash uchun soxta (mock) ma'lumot qaytaruvchi provider.

Real Instagram integratsiyasi qo'shilganda, bu fayl o'rniga
integrations/instagram.py to'ldiriladi — CORE kod o'zgarmaydi,
chunki ikkalasi ham bir xil MessagingProvider interfeysidan foydalanadi.
"""

from integrations.base import MessagingProvider
from typing import List, Dict


class MockInstagramProvider(MessagingProvider):
    """Test uchun soxta Instagram xabar/komment provideri."""

    def get_messages(self) -> List[Dict]:
        return [
            {"chat_id": "mock_user_1", "text": "Samokat narxi qancha?"},
            {"chat_id": "mock_user_2", "text": "Yetkazib berish bormi?"},
        ]

    def get_comments(self) -> List[Dict]:
        """Instagram'ga xos qo'shimcha metod — postlar ostidagi kommentlar."""
        return [
            {"post_id": "mock_post_1", "user": "mock_user_3", "text": "Zo'r mahsulot!"},
        ]

    def send_message(self, chat_id: str, text: str) -> bool:
        print(f"[MOCK INSTAGRAM] -> {chat_id}: {text}")
        return True

    def reply_comment(self, post_id: str, text: str) -> bool:
        print(f"[MOCK INSTAGRAM COMMENT REPLY] -> post {post_id}: {text}")
        return True
