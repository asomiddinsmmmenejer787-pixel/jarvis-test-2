"""
ai/mock.py
----------
Internet yoki API key bo'lmasa ham dasturni sinash uchun soxta AI provider.
Haqiqiy javob bermaydi — faqat tuzilma to'g'ri ishlayotganini tekshirish uchun.
"""

from ai.base import AIProvider


class MockProvider(AIProvider):
    """Test uchun soxta javob qaytaruvchi provider."""

    def generate_text(self, system_instruction: str, user_message: str) -> str:
        return (
            "[MOCK JAVOB]\n"
            f"Agar bu haqiqiy Gemini bo'lganida, quyidagi so'rovga javob berardi:\n"
            f"\"{user_message}\"\n"
            "Haqiqiy javob olish uchun .env faylida AI_PROVIDER=gemini "
            "va GEMINI_API_KEY ni to'g'ri kiriting."
        )
