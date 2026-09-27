"""
ai/base.py
----------
Barcha AI provider'lar (Gemini, Mock, kelajakda boshqalar) shu interfeysga
rioya qilishi kerak. Shu tufayli Jarvis'ning qolgan qismi qaysi AI
ishlatilayotganini bilishi shart emas — faqat generate_text() chaqiradi.
"""

from abc import ABC, abstractmethod


class AIProvider(ABC):
    """Barcha AI provayderlar uchun umumiy shablon (interfeys)."""

    @abstractmethod
    def generate_text(self, system_instruction: str, user_message: str) -> str:
        """
        Berilgan system_instruction (Jarvis'ning "shaxsi") va foydalanuvchi
        xabari asosida javob matnini qaytaradi.

        Args:
            system_instruction: AI qanday xatti-harakat qilishi kerakligini belgilaydi.
            user_message: Foydalanuvchining haqiqiy so'rovi.

        Returns:
            AI tomonidan generatsiya qilingan matn.
        """
        raise NotImplementedError
