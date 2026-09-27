"""
integrations/base.py
---------------------
Instagram va Telegram kabi xabar almashish xizmatlari uchun umumiy interfeys.
Hozircha faqat Mock (soxta) versiyalari mavjud — real API keyingi bosqichlarda
qo'shiladi, lekin CORE kod o'zgarmasdan qoladi, chunki hammasi shu interfeys
orqali ishlaydi.
"""

from abc import ABC, abstractmethod
from typing import List, Dict


class MessagingProvider(ABC):
    """Barcha messaging integratsiyalar uchun umumiy shablon."""

    @abstractmethod
    def get_messages(self) -> List[Dict]:
        """Yangi kelgan xabarlar ro'yxatini qaytaradi."""
        raise NotImplementedError

    @abstractmethod
    def send_message(self, chat_id: str, text: str) -> bool:
        """Berilgan chat/foydalanuvchiga xabar yuboradi. Muvaffaqiyatli bo'lsa True."""
        raise NotImplementedError
